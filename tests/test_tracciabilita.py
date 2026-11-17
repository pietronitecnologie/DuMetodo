import datetime as dt
import io
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from dumetodo.configurazione import servizi as config
from dumetodo.core.models import Reparto
from dumetodo.ordini.models import Ordine
from dumetodo.tracciabilita import models as modelli
from dumetodo.tracciabilita import servizi
from dumetodo.tracciabilita.models import (
    Chiave,
    Controllo,
    ControlloQualita,
    Elemento,
    EsitoControllo,
    FileOrdine,
    Postazione,
    Reclamo,
    StatoWorkOrder,
    TipoControllo,
    TipoElemento,
    Traccia,
    WorkOrderBatteria,
)
from dumetodo.tracciabilita.servizi import OperazioneNonConsentita

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
PDF = b"%PDF-1.4\n" + b"x" * 100
API = "/tracciabilita/api/cavi/"


@pytest.fixture
def reparto(db):
    return Reparto.objects.create(codice="GK01-CVI", nome="Cavi")


@pytest.fixture
def reparto_pulsanti(db):
    return Reparto.objects.create(codice="GK01-PLS", nome="Pulsanti")


@pytest.fixture
def postazione(reparto, amministratore):
    p = Postazione.objects.create(codice="GKAST-107", nome="Taglierina", reparto=reparto)
    p.token = servizi.genera_token_postazione(p.pk, amministratore)
    return p


def _xlsx(response) -> list[tuple]:
    assert response["Content-Type"].startswith("application/vnd.openxmlformats")
    ws = load_workbook(io.BytesIO(response.content)).active
    return list(ws.iter_rows(min_row=2, values_only=True))


def _traccia(elemento=None, ordine=None, giorni_fa=0, chiave="X", valore="", **kw):
    data = timezone.make_aware(
        dt.datetime.combine(timezone.localdate() - dt.timedelta(days=giorni_fa), dt.time(23, 30))
    )
    return Traccia.objects.create(elemento=elemento, ordine=ordine, data=data, chiave=chiave, valore=valore, **kw)


# =========================================================================== ordini
def test_risolvi_ordine_per_chiave_completa(ordine):
    altro_anno = Ordine.objects.create(serie="00040", numero="0004300", appendice="", anno=2025)
    con_appendice = Ordine.objects.create(serie="00040", numero="0004300", appendice="T1", anno=2026)
    assert servizi.risolvi_ordine("40.0004300-2026") == ordine
    assert servizi.risolvi_ordine("00040.0004300-2026") == ordine
    assert servizi.risolvi_ordine("00040.4300-2026") == ordine
    assert servizi.risolvi_ordine("00040.0004300.0.2026") == ordine  # chiave ticket, appendice "0"
    assert servizi.risolvi_ordine("40.0004300-2025") == altro_anno
    assert servizi.risolvi_ordine("40.0004300-T1-2026") == con_appendice
    assert servizi.risolvi_ordine("00040.0004300.T1-2026") == con_appendice
    with pytest.raises(OperazioneNonConsentita, match="non trovato"):
        servizi.risolvi_ordine("40.0004300-2024")
    with pytest.raises(OperazioneNonConsentita, match="non valido"):
        servizi.risolvi_ordine("4300")  # niente ricerche "contains" sul solo numero


# =========================================================================== batterie
def test_primo_ingresso_genera_codice_con_prefisso_configurato(operatore, reparto, ordine):
    config.scrivi("tracciabilita.prefisso_codice_batterie", "XB")
    config.invalida_cache()
    t = servizi.registra_batteria(
        utente=operatore, azione=Chiave.INGRESSO, data=timezone.localdate(), reparto=reparto, ordine=ordine
    )
    assert re.fullmatch(r"XB[0-9A-F]{6}", t.elemento.codice)
    assert t.elemento.tipo == TipoElemento.BATTERIA and t.ordine == ordine and t.inserita_da == operatore
    assert t.testo_barcode == f"{t.elemento.codice},IN,{timezone.localdate():%d/%m/%Y}"


def test_prefisso_predefinito_senza_riferimenti_al_cliente(operatore, reparto):
    t = servizi.registra_batteria(utente=operatore, azione="IN", data=timezone.localdate(), reparto=reparto)
    assert t.elemento.codice.startswith("BT")


def test_ingresso_di_codice_esistente_e_ricarica_di_sconosciuta_rifiutati(operatore, reparto):
    oggi = timezone.localdate()
    servizi.registra_batteria(utente=operatore, azione="IN", data=oggi, reparto=reparto, codice="BAT1")
    with pytest.raises(OperazioneNonConsentita, match="esistente"):
        servizi.registra_batteria(utente=operatore, azione="IN", data=oggi, reparto=reparto, codice="BAT1")
    with pytest.raises(OperazioneNonConsentita, match="sconosciuta"):
        servizi.registra_batteria(utente=operatore, azione="RC", data=oggi, reparto=reparto, codice="NUOVA")
    assert not Elemento.objects.filter(codice="NUOVA").exists()
    rc = servizi.registra_batteria(utente=operatore, azione="RC", data=oggi, reparto=reparto, codice="BAT1")
    assert rc.elemento.tracce.count() == 2 and Elemento.objects.count() == 1


def test_data_utente_rispettata(operatore, reparto):
    ieri = timezone.localdate() - dt.timedelta(days=1)
    t = servizi.registra_batteria(utente=operatore, azione="IN", data=ieri, reparto=reparto, codice="B2")
    assert timezone.localtime(t.data).date() == ieri


def test_cliente_non_puo_registrare(cliente, reparto):
    with pytest.raises(OperazioneNonConsentita, match="permessi"):
        servizi.registra_batteria(utente=cliente, azione="IN", data=timezone.localdate(), reparto=reparto)


def test_codice_generato_univoco_con_nuovi_tentativi(operatore, monkeypatch):
    Elemento.objects.create(codice="BTAAAAAA")
    valori = iter(["aaaaaa", "aaaaaa", "bbbbbb"])
    monkeypatch.setattr(servizi.secrets, "token_hex", lambda n: next(valori))
    assert servizi.genera_codice("BT") == "BTBBBBBB"


def test_collisione_in_creazione_si_riprova(operatore, monkeypatch):
    # Due richieste parallele ricevono la stessa proposta: il vincolo del database decide, la seconda riprova.
    Elemento.objects.create(codice="BT111111")
    proposte = iter(["BT111111", "BT222222"])
    monkeypatch.setattr(servizi, "genera_codice", lambda prefisso: next(proposte))
    e = servizi._crea_elemento_generato("BT", TipoElemento.BATTERIA, operatore)
    assert e.codice == "BT222222"


def test_ricarica_chiude_il_work_order(operatore, reparto):
    servizi.registra_batteria(
        utente=operatore, azione="IN", data=timezone.localdate() - dt.timedelta(days=200), reparto=reparto, codice="B9"
    )
    assert servizi.genera_work_order_batterie() == 1
    assert servizi.genera_work_order_batterie() == 0  # un solo work order aperto per batteria
    wo = WorkOrderBatteria.objects.get()
    assert wo.stato == StatoWorkOrder.APERTO and wo.ultima_traccia.chiave == "IN"
    servizi.registra_batteria(utente=operatore, azione="RC", data=timezone.localdate(), reparto=reparto, codice="B9")
    wo.refresh_from_db()
    assert wo.stato == StatoWorkOrder.CHIUSO and wo.chiuso_da == operatore
    assert servizi.genera_work_order_batterie() == 0  # ricaricata di recente


def test_chiusura_work_order_via_post(client, operatore, reparto):
    e = Elemento.objects.create(codice="B5", tipo=TipoElemento.BATTERIA)
    wo = WorkOrderBatteria.objects.create(elemento=e)
    client.force_login(operatore)
    assert client.get(reverse("tracciabilita:work_order_chiudi", args=[wo.pk])).status_code == 405
    client.post(reverse("tracciabilita:work_order_chiudi", args=[wo.pk]))
    wo.refresh_from_db()
    assert wo.stato == StatoWorkOrder.CHIUSO
    client.logout()
    r = client.post(reverse("tracciabilita:work_order_genera"))
    assert r.status_code == 302 and "accesso" in r["Location"]


# =========================================================================== tracce generiche e lotti
def test_traccia_generica_salva_il_valore_e_accoda_eventi(operatore, reparto, ordine):
    oggi = timezone.localdate()
    t1 = servizi.registra_traccia(
        utente=operatore, codice="EL1", chiave="Collaudo", valore="OK 12V", data=oggi, reparto=reparto, ordine=ordine
    )
    assert t1.valore == "OK 12V"
    t2 = servizi.registra_traccia(
        utente=operatore, codice="EL1", chiave="Imballo", valore="", data=oggi, reparto=reparto
    )
    assert t2.elemento == t1.elemento and t1.elemento.tracce.count() == 2
    assert t1.testo_barcode == f"EL1,Collaudo,OK 12V,{oggi:%d/%m/%Y}"
    with pytest.raises(OperazioneNonConsentita, match="riservata"):
        servizi.registra_traccia(utente=operatore, codice="EL1", chiave="in", valore="", data=oggi, reparto=reparto)


def test_lotto_busta_senza_doppioni(operatore, ordine, reparto_pulsanti):
    create = servizi.registra_lotto_busta(ordine.pk, "L-77", "B-3", operatore)
    assert {(t.chiave, t.valore) for t in create} == {("LOTTO", "L-77"), ("BUSTA", "B-3")}
    assert all(t.ordine == ordine and t.reparto == reparto_pulsanti for t in create)
    assert servizi.registra_lotto_busta(ordine.pk, "L-77", "B-3", operatore) == []
    assert len(servizi.registra_lotto_busta(ordine.pk, "L-78", "", operatore)) == 1
    with pytest.raises(OperazioneNonConsentita, match="lotto o la busta"):
        servizi.registra_lotto_busta(ordine.pk, " ", "", operatore)


def test_lotto_busta_da_pagina_ordine_htmx(client, operatore, ordine):
    client.force_login(operatore)
    url = reverse("tracciabilita:lotto_busta", args=[ordine.pk])
    assert client.get(url).status_code == 405
    r = client.post(url, {"lotto": "L1", "busta": ""}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "Tracciatura creata" in r.content.decode()
    assert ordine.tracce.get().valore == "L1"


# =========================================================================== API postazioni (cavi)
def test_token_conservato_solo_come_impronta(postazione):
    postazione.refresh_from_db()
    assert postazione.token and postazione.token not in postazione.token_hash and len(postazione.token_hash) == 64
    assert servizi.postazione_da_token(postazione.token) == postazione
    assert servizi.postazione_da_token("sbagliato") is None


def test_api_senza_token_o_con_token_errato(client, postazione, ordine):
    dati = {"ordine": "40.0004300-2026", "prodotto": "CV1", "misura": "120", "data": "2026/03/01,10:00:00"}
    assert client.post(API, dati).status_code == 401
    assert client.post(API, dati, headers={"X-Postazione-Token": "x" * 43}).status_code == 401
    postazione.attiva = False
    postazione.save()
    assert client.post(API, dati, headers={"X-Postazione-Token": postazione.token}).status_code == 401
    assert not Traccia.objects.exists()


def test_api_crea_traccia_cavo_e_deduplica(client, postazione, ordine):
    dati = {
        "ordine": "00040.0004300-2026",
        "lottoCavo": "LOT-9",
        "prodotto": "CV-AWG18",
        "misura": "1250",
        "data": "2026/03/01,10:15:30",
        "asset": "GKAST-107",
    }
    r = client.post(API, dati, headers={"X-Postazione-Token": postazione.token})
    assert r.status_code == 201, r.content
    corpo = r.json()
    t = Traccia.objects.get(pk=corpo["id"])
    assert corpo["esito"] == "creata" and t.ordine == ordine and t.postazione == postazione
    assert (t.chiave, t.valore, t.elemento.lotto, t.elemento.tipo) == ("CV-AWG18", "1250", "LOT-9", TipoElemento.CAVO)
    assert t.reparto == postazione.reparto
    assert timezone.localtime(t.data).replace(tzinfo=None) == dt.datetime(2026, 3, 1, 10, 15, 30)
    r2 = client.post(API, dati, headers={"X-Postazione-Token": postazione.token})
    assert r2.status_code == 200 and r2.json() == {**corpo, "esito": "duplicata"}
    assert Traccia.objects.count() == 1 and Elemento.objects.count() == 1


def test_api_ordine_sconosciuto_non_crea_ordini(client, postazione, ordine):
    n = Ordine.objects.count()
    dati = {"ordine": "00040.0009999-2026", "prodotto": "CV", "misura": "1", "data": "2026/03/01"}
    r = client.post(API, dati, headers={"X-Postazione-Token": postazione.token})
    assert r.status_code == 404 and "non trovato" in r.json()["errore"]
    assert Ordine.objects.count() == n and not Traccia.objects.exists()
    dati["ordine"] = "boh"
    assert client.post(API, dati, headers={"X-Postazione-Token": postazione.token}).status_code == 400


def test_api_valida_gli_ingressi(client, postazione, ordine):
    base = {"ordine": "40.0004300-2026", "prodotto": "CV", "misura": "1", "data": "2026/03/01"}
    h = {"X-Postazione-Token": postazione.token}
    assert client.post(API, {**base, "data": "ieri"}, headers=h).status_code == 400
    assert client.post(API, {**base, "misura": ""}, headers=h).status_code == 400
    assert client.post(API, {**base, "asset": "ALTRO"}, headers=h).status_code == 400
    assert client.post(API, {**base, "location": "NESSUNO"}, headers=h).status_code == 400
    r = client.post(API, {**base, "serie": "40", "numero": "4300", "anno": "2026", "ordine": ""}, headers=h)
    assert r.status_code == 201


def test_api_get_solo_se_abilitata(client, postazione, ordine):
    query = f"?token={postazione.token}&ordine=40.0004300-2026&prodotto=CV&misura=5&data=2026/03/02"
    assert client.get(API + query).status_code == 405
    config.scrivi("tracciabilita.api_consenti_get", True)
    config.invalida_cache()
    assert client.get(API + query).status_code == 201


def test_intestatura_sul_cavo_esistente(client, operatore, postazione, ordine):
    t, _ = servizi.registra_cavo_da_postazione(
        postazione, ordine=ordine, prodotto="CV", misura="10", lotto="L", data=timezone.now(), reparto=None
    )
    client.force_login(operatore)
    url = reverse("tracciabilita:cavo_intestato", args=[t.elemento_id])
    assert client.get(url).status_code == 405
    client.post(url)
    i = t.elemento.tracce.get(chiave=Chiave.INTESTATO)
    assert i.ordine == ordine and Elemento.objects.count() == 1
    with pytest.raises(OperazioneNonConsentita, match="già intestato"):
        servizi.registra_intestatura(t.elemento_id, operatore)


# =========================================================================== liste ed esportazioni
def test_esportazione_batterie_rispetta_i_filtri_e_include_il_giorno_finale(client, operatore):
    b1 = Elemento.objects.create(codice="BX1", tipo=TipoElemento.BATTERIA)
    b2 = Elemento.objects.create(codice="BX2", tipo=TipoElemento.BATTERIA)
    _traccia(b1, chiave="IN", giorni_fa=0)  # oggi alle 23:30: con il vecchio filtro andava persa
    _traccia(b2, chiave="IN", giorni_fa=10)
    _traccia(Elemento.objects.create(codice="GEN1"), chiave="IN", giorni_fa=0)
    client.force_login(operatore)
    oggi = timezone.localdate()
    url = reverse("tracciabilita:batterie")
    righe = _xlsx(
        client.get(url, {"dal": (oggi - dt.timedelta(days=1)).isoformat(), "al": oggi.isoformat(), "formato": "xlsx"})
    )
    assert [r[1] for r in righe] == ["BX1"]
    righe = _xlsx(client.get(url, {"codice": "BX2", "formato": "xlsx"}))
    assert [r[1] for r in righe] == ["BX2"]
    assert len(_xlsx(client.get(url, {"formato": "xlsx"}))) == 2
    pagina = client.get(url, {"codice": "BX1"}).content.decode()
    assert "BX1" in pagina and "BX2" not in pagina and "formato=xlsx" in pagina


def test_lista_tracce_mostra_di_default_gli_ultimi_giorni(client, operatore):
    _traccia(Elemento.objects.create(codice="RECENTE"), giorni_fa=1)
    _traccia(Elemento.objects.create(codice="VECCHIA"), giorni_fa=30)
    client.force_login(operatore)
    pagina = client.get(reverse("tracciabilita:tracce")).content.decode()
    assert "RECENTE" in pagina and "VECCHIA" not in pagina
    righe = _xlsx(client.get(reverse("tracciabilita:tracce"), {"formato": "xlsx"}))
    assert [r[1] for r in righe] == ["RECENTE"]
    r = client.get(reverse("tracciabilita:tracce"), {"dal": "", "al": ""})
    assert "VECCHIA" in r.content.decode()
    r = client.get(reverse("tracciabilita:tracce"), {"dal": "2026-05-02", "al": "2026-05-01"})
    assert r.status_code == 200 and "successiva" in r.content.decode()


def test_esportazione_cavi_tiene_prodotto_e_misura(client, cliente, postazione, ordine):
    servizi.registra_cavo_da_postazione(
        postazione, ordine=ordine, prodotto="CV-X", misura="300", lotto="L1", data=timezone.now(), reparto=None
    )
    client.force_login(cliente)
    righe = _xlsx(client.get(reverse("tracciabilita:cavi"), {"formato": "xlsx"}))
    assert len(righe) == 1 and righe[0][6:8] == ("CV-X", "300") and righe[0][3] == "L1"


# =========================================================================== permessi delle viste
@pytest.mark.parametrize(
    "nome",
    ["batterie", "cavi", "qualita", "reclami", "display"],
)
def test_liste_visibili_a_fornitore_e_cliente(client, operatore, cliente, nome):
    for u in (operatore, cliente):
        client.force_login(u)
        assert client.get(reverse(f"tracciabilita:{nome}")).status_code == 200


@pytest.mark.parametrize(
    "nome",
    [
        "tracce",
        "file_lista",
        "batteria_nuova",
        "traccia_nuova",
        "qualita_nuovo",
        "reclamo_nuovo",
        "display_nuovo",
        "file_carica",
        "work_order",
        "genera_codice",
    ],
)
def test_pagine_del_fornitore(client, operatore, cliente, nome):
    url = reverse(f"tracciabilita:{nome}")
    assert client.get(url).status_code == 302  # anonimo -> login
    client.force_login(cliente)
    assert client.get(url).status_code == 403
    client.force_login(operatore)
    assert client.get(url).status_code == 200


def test_postazioni_e_token_solo_amministratore(client, operatore, amministratore, reparto):
    p = Postazione.objects.create(codice="BANCO1", nome="Banco", reparto=reparto)
    client.force_login(operatore)
    assert client.get(reverse("tracciabilita:postazioni")).status_code == 403
    assert client.post(reverse("tracciabilita:postazione_token", args=[p.pk])).status_code == 403
    client.force_login(amministratore)
    r = client.post(reverse("tracciabilita:postazione_token", args=[p.pk]))
    token = re.search(r"user-select-all\">([^<]+)<", r.content.decode()).group(1)
    assert servizi.postazione_da_token(token) == p and r["Cache-Control"] == "no-store"
    client.post(reverse("tracciabilita:postazione_revoca", args=[p.pk]))
    assert servizi.postazione_da_token(token) is None


def test_creazione_batteria_da_vista_e_pagina_con_barcode(client, operatore, reparto):
    client.force_login(operatore)
    r = client.post(
        reverse("tracciabilita:batteria_nuova"),
        {"azione": "IN", "codice": "", "data": timezone.localdate().isoformat(), "reparto": reparto.pk},
    )
    t = Traccia.objects.get()
    assert r.status_code == 302 and r["Location"] == t.get_absolute_url()
    pagina = client.get(r["Location"]).content.decode()
    assert f'jsbarcode-value="{t.testo_barcode}"' in pagina and "JsBarcode.all.min.js" in pagina
    r = client.post(
        reverse("tracciabilita:batteria_nuova"),
        {
            "azione": "IN",
            "codice": "X",
            "data": (timezone.localdate() + dt.timedelta(days=3)).isoformat(),
            "reparto": reparto.pk,
        },
    )
    assert r.status_code == 200 and "futuro" in r.content.decode()


def test_genera_codice_htmx(client, operatore):
    client.force_login(operatore)
    r = client.get(reverse("tracciabilita:genera_codice"))
    assert re.search(r'value="BT[0-9A-F]{6}"', r.content.decode())
    assert not Elemento.objects.exists()  # una proposta non crea nulla


def test_stampa_barcode_escape_e_parametro_obbligatorio(client, operatore):
    client.force_login(operatore)
    assert client.get(reverse("tracciabilita:stampa")).status_code == 400
    r = client.get(reverse("tracciabilita:stampa"), {"barcode": 'A1,"><script>alert(1)</script>'})
    pagina = r.content.decode()
    assert r.status_code == 200 and "<script>alert" not in pagina and "&quot;&gt;&lt;script&gt;" in pagina


def test_cliente_non_vede_tracce_generiche_senza_ordine(client, cliente, ordine):
    gen = _traccia(Elemento.objects.create(codice="SEGRETO"))
    con_ordine = _traccia(ordine=ordine, chiave="LOTTO", valore="L")
    client.force_login(cliente)
    assert client.get(gen.get_absolute_url()).status_code == 404
    assert client.get(con_ordine.get_absolute_url()).status_code == 200


# =========================================================================== pagina dell'ordine e file
def test_pagina_ordine_include_il_pannello(client, operatore, cliente, ordine):
    client.force_login(operatore)
    pagina = client.get(ordine.get_absolute_url()).content.decode()
    assert reverse("tracciabilita:pannello_ordine", args=[ordine.pk]) in pagina
    servizi.registra_lotto_busta(ordine.pk, "LOTTO-9", "", operatore)
    r = client.get(reverse("tracciabilita:pannello_ordine", args=[ordine.pk]))
    assert "LOTTO-9" in r.content.decode() and "Salva lotto" in r.content.decode()
    client.force_login(cliente)
    r = client.get(reverse("tracciabilita:pannello_ordine", args=[ordine.pk]))
    assert "LOTTO-9" in r.content.decode() and "Salva lotto" not in r.content.decode()


def test_caricamento_file_e_accesso_controllato(client, operatore, cliente, amministratore, caporeparto, ordine):
    client.force_login(operatore)
    url = reverse("tracciabilita:file_ordine_carica", args=[ordine.pk])
    r = client.post(
        url,
        {"file": [SimpleUploadedFile("foto.jpg", JPEG, "image/jpeg"), SimpleUploadedFile("../../doc.pdf", PDF)]},
    )
    assert r.status_code == 302
    foto, doc = FileOrdine.objects.order_by("tipo_contenuto")[1], FileOrdine.objects.order_by("tipo_contenuto")[0]
    assert (foto.tipo_contenuto, doc.tipo_contenuto) == ("image/jpeg", "application/pdf")
    assert doc.nome_originale == "doc.pdf" and doc.file.name.startswith(f"ordini/{ordine.pk}/")
    assert ".." not in doc.file.name
    client.logout()
    r = client.get(foto.get_absolute_url())
    assert r.status_code == 302  # anonimo: nessun accesso diretto
    client.force_login(cliente)
    r = client.get(foto.get_absolute_url())
    assert r.status_code == 200 and b"".join(r.streaming_content) == JPEG and r["Content-Type"] == "image/jpeg"
    # eliminazione: solo l'autore o un amministratore, solo POST
    client.force_login(caporeparto)
    client.post(reverse("tracciabilita:file_elimina", args=[foto.pk]))
    assert FileOrdine.objects.filter(pk=foto.pk).exists()
    client.force_login(amministratore)
    assert client.get(reverse("tracciabilita:file_elimina", args=[foto.pk])).status_code == 405
    client.post(reverse("tracciabilita:file_elimina", args=[foto.pk]))
    assert not FileOrdine.objects.filter(pk=foto.pk).exists()


def test_file_non_ammessi_o_troppo_grandi(operatore, ordine):
    finto = SimpleUploadedFile("foto.jpg", b"<html><script>alert(1)</script>", "image/jpeg")
    with pytest.raises(OperazioneNonConsentita, match="non ammesso"):
        servizi.carica_file_ordine(ordine.pk, [finto], operatore)
    config.scrivi("tracciabilita.dimensione_massima_file_mb", 1)
    config.invalida_cache()
    grande = SimpleUploadedFile("g.jpg", JPEG + b"\x00" * (1024 * 1024), "image/jpeg")
    with pytest.raises(OperazioneNonConsentita, match="dimensione massima"):
        servizi.carica_file_ordine(ordine.pk, [SimpleUploadedFile("ok.jpg", JPEG), grande], operatore)
    assert not FileOrdine.objects.exists()  # tutto o niente


def test_pagina_carica_foto_con_codice_ordine(client, operatore, ordine):
    client.force_login(operatore)
    url = reverse("tracciabilita:file_carica")
    r = client.post(url, {"ordine": "40.0004300-2026", "file": SimpleUploadedFile("a.jpg", JPEG)})
    assert r.status_code == 302 and FileOrdine.objects.get().ordine == ordine
    r = client.post(url, {"ordine": "40.0000001-2026", "file": SimpleUploadedFile("a.jpg", JPEG)}, follow=True)
    assert "non trovato" in r.content.decode() and FileOrdine.objects.count() == 1
    lista = client.get(reverse("tracciabilita:file_lista")).content.decode()
    assert "a.jpg" in lista


# =========================================================================== controllo qualità
@pytest.fixture
def test_qualita(db):
    visivo = Controllo.objects.create(codice="visivo", nome="Visivo", tipo=TipoControllo.QUANTITA)
    t = modelli.Test.objects.create(codice="pulsanti", nome="Pulsanti")
    t.controlli.add(visivo, Controllo.objects.get(codice="distinta"), Controllo.objects.get(codice="disegno"))
    return t


def test_controlli_distinta_e_disegno_creati_dalla_migrazione(db):
    assert set(Controllo.objects.filter(tipo=TipoControllo.PRESENZA).values_list("codice", flat=True)) >= {
        "distinta",
        "disegno",
    }


def test_registra_controllo_qualita(operatore, ordine, test_qualita):
    cq = servizi.registra_controllo_qualita(
        utente=operatore,
        test=test_qualita,
        articolo="ART-A",
        data=timezone.localdate(),
        ordine=ordine,
        risultati={"campione_visivo": "20", "ko_visivo": "1", "presente_distinta": "on"},
    )
    esiti = {e.controllo.codice: e for e in cq.esiti.all()}
    assert (esiti["visivo"].campione, esiti["visivo"].ko, esiti["visivo"].presente) == (20, 1, None)
    assert esiti["distinta"].presente is True and esiti["disegno"].presente is False
    assert cq.eseguito_da == operatore and ordine.controlli_qualita.get() == cq


@pytest.mark.parametrize(
    ("risultati", "errore"),
    [
        ({"campione_visivo": "5", "ko_visivo": "6"}, "superano"),
        ({"campione_visivo": "cinque", "ko_visivo": "0"}, "numero intero"),
        ({"campione_visivo": "5"}, "numero intero"),
        ({"campione_visivo": "-1", "ko_visivo": "0"}, "numero intero"),
    ],
)
def test_controllo_qualita_valori_non_validi(operatore, ordine, test_qualita, risultati, errore):
    with pytest.raises(OperazioneNonConsentita, match=errore):
        servizi.registra_controllo_qualita(
            utente=operatore, test=test_qualita, articolo="A", data=timezone.localdate(), ordine=ordine,
            risultati=risultati,
        )  # fmt: skip
    assert not ControlloQualita.objects.exists()


def test_controllo_qualita_da_vista_su_stock_ed_esportazione(client, operatore, cliente, test_qualita):
    client.force_login(operatore)
    r = client.get(reverse("tracciabilita:qualita_controlli"), {"test": "pulsanti"})
    pagina = r.content.decode()
    assert 'name="campione_visivo"' in pagina and 'name="presente_distinta" id="presente_distinta" checked' in pagina
    dati = {
        "riferimento": "STOCK-1",
        "articolo": "ART-Z",
        "data": timezone.localdate().isoformat(),
        "test": "pulsanti",
        "campione_visivo": "10",
        "ko_visivo": "0",
    }
    assert client.post(reverse("tracciabilita:qualita_nuovo"), dati).status_code == 302
    cq = ControlloQualita.objects.get()
    assert cq.ordine is None and cq.riferimento == "STOCK-1"
    r = client.post(reverse("tracciabilita:qualita_nuovo"), {**dati, "riferimento": ""})
    assert r.status_code == 200 and "riferimento di stock" in r.content.decode()
    client.force_login(cliente)
    righe = _xlsx(client.get(reverse("tracciabilita:qualita"), {"articolo": "ART-Z", "formato": "xlsx"}))
    assert len(righe) == 3 and {r[5] for r in righe} == {"Visivo", "Distinta", "Disegno"}
    assert _xlsx(client.get(reverse("tracciabilita:qualita"), {"articolo": "ALTRO", "formato": "xlsx"})) == []
    assert EsitoControllo.objects.count() == 3


# =========================================================================== reclami e test display
def test_reclamo_conserva_la_data_dell_utente_e_l_allegato(client, operatore, cliente, ordine, reparto):
    client.force_login(operatore)
    data = timezone.localdate() - dt.timedelta(days=5)
    r = client.post(
        reverse("tracciabilita:reclamo_nuovo"),
        {
            "codice": "RC-1",
            "ordine": "40.0004300-2026",
            "articolo": "ART-A",
            "reparto": reparto.pk,
            "data": data.isoformat(),
            "note": "graffi",
            "file": SimpleUploadedFile("foto.jpg", JPEG),
        },
    )
    assert r.status_code == 302
    rec = Reclamo.objects.get()
    assert rec.data == data and rec.ordine == ordine and rec.file_tipo == "image/jpeg"
    client.force_login(cliente)
    r = client.get(reverse("tracciabilita:reclamo_file", args=[rec.pk]))
    assert r.status_code == 200 and "attachment" in r["Content-Disposition"]
    righe = _xlsx(client.get(reverse("tracciabilita:reclami"), {"codice": "RC-1", "formato": "xlsx"}))
    assert righe[0][1:3] == ("RC-1", ordine.codice_breve)


def test_test_display_con_campi_appiccicosi(client, operatore):
    client.force_login(operatore)
    oggi = timezone.localdate().isoformat()
    dati = {
        "prodotto": "RAFFAELLO28",
        "sito": "S1",
        "articolo": "A1",
        "esito": "NEG",
        "fw": "1.2",
        "lotto": "L5",
        "data": oggi,
    }
    r = client.post(reverse("tracciabilita:display_nuovo"), dati)
    assert r.status_code == 302 and "fw=1.2" in r["Location"] and "lotto=L5" in r["Location"]
    pagina = client.get(r["Location"]).content.decode()
    assert 'value="L5"' in pagina and "tracciabilita/display.js" in pagina
    t = modelli.TestDisplay.objects.get()
    assert t.utente == operatore and t.get_esito_display() == "Negativo"


def test_test_display_lettura_sito_articolo_anche_senza_javascript(client, operatore):
    client.force_login(operatore)
    oggi = timezone.localdate().isoformat()
    dati = {"prodotto": "RAFFAELLO28", "sito": "S9$$ART-9", "articolo": "", "esito": "", "data": oggi}
    client.post(reverse("tracciabilita:display_nuovo"), dati)
    t = modelli.TestDisplay.objects.get()
    assert (t.sito, t.articolo, t.esito) == ("S9", "ART-9", "POS")


def test_test_display_prodotto_validato_ed_esportazione_filtrata(client, operatore):
    client.force_login(operatore)
    oggi = timezone.localdate().isoformat()
    r = client.post(
        reverse("tracciabilita:display_nuovo"),
        {"prodotto": "INVENTATO", "sito": "S", "articolo": "A", "esito": "POS", "data": oggi},
    )
    assert r.status_code == 200 and not modelli.TestDisplay.objects.exists()
    for sito, esito in (("S1", "POS"), ("S2", "NEG")):
        modelli.TestDisplay.objects.create(sito=sito, articolo="A", esito=esito, data=timezone.localdate())
    righe = _xlsx(client.get(reverse("tracciabilita:display"), {"esito": "NEG", "formato": "xlsx"}))
    assert [r[2] for r in righe] == ["S2"]
