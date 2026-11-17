import datetime as dt
import io

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
    Controllo,
    ControlloQualita,
    Elemento,
    EsitoControllo,
    FileOrdine,
    TipoControllo,
    Traccia,
)
from dumetodo.tracciabilita.servizi import OperazioneNonConsentita

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
PDF = b"%PDF-1.4\n" + b"x" * 100


@pytest.fixture
def reparto(db):
    return Reparto.objects.create(codice="GK01-CVI", nome="Cavi")


@pytest.fixture
def reparto_pulsanti(db):
    return Reparto.objects.create(codice="GK01-PLS", nome="Pulsanti")


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
def test_data_utente_rispettata(operatore, reparto):
    ieri = timezone.localdate() - dt.timedelta(days=1)
    t = servizi.registra_traccia(utente=operatore, codice="B2", chiave="X", valore="1", data=ieri, reparto=reparto)
    assert timezone.localtime(t.data).date() == ieri


def test_cliente_non_puo_registrare(cliente, reparto):
    with pytest.raises(OperazioneNonConsentita, match="permessi"):
        servizi.registra_traccia(
            utente=cliente, codice="B3", chiave="X", valore="", data=timezone.localdate(), reparto=reparto
        )


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
        servizi.registra_traccia(utente=operatore, codice="EL1", chiave="lotto", valore="", data=oggi, reparto=reparto)


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
# =========================================================================== liste ed esportazioni
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


# =========================================================================== permessi delle viste
@pytest.mark.parametrize(
    "nome",
    ["qualita"],
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
        "traccia_nuova",
        "qualita_nuovo",
        "file_carica",
    ],
)
def test_pagine_del_fornitore(client, operatore, cliente, nome):
    url = reverse(f"tracciabilita:{nome}")
    assert client.get(url).status_code == 302  # anonimo -> login
    client.force_login(cliente)
    assert client.get(url).status_code == 403
    client.force_login(operatore)
    assert client.get(url).status_code == 200


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
