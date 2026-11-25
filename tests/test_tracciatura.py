"""Tracciatura configurabile: modelli e campi, registrazione con esito, collegamenti, file, pagine e permessi."""

import io
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile

from metodo.anagrafica.models import Articolo
from metodo.consegne.models import DDT
from metodo.core.permessi import applica_profilo
from metodo.magazzino.models import Componente
from metodo.ordini.models import Ordine, RigaOrdine
from metodo.ordini.servizi import OperazioneNonConsentita
from metodo.tracciatura import servizi
from metodo.tracciatura.models import Campo, Esito, Tracciatura

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def capo(db):
    u = User.objects.create_user("qualita")
    applica_profilo(u, "Amministratore")
    return User.objects.get(pk=u.pk)


@pytest.fixture
def media(settings, tmp_path):
    settings.STORAGES = {**settings.STORAGES, "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}}
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def collaudo(capo):
    """Modello «Collaudo pulsantiera» per i codici 18090*, con un campo per tipo."""
    m = servizi.salva_modello(
        capo,
        None,
        {
            "nome": "Collaudo pulsantiera",
            "categoria": "QUA",
            "uso_ordine": "O",
            "uso_articolo": "F",
            "uso_lotto": "F",
            "codici": "18090*\n18090*\n",
            "descrizione": "Verifica a fine linea.",
        },
    )
    campi = {
        "coppia": servizi.salva_campo(
            capo,
            m.pk,
            None,
            {
                "etichetta": "Coppia",
                "tipo": "DEC",
                "unita": "Nm",
                "minimo": "1,5",
                "massimo": "2.5",
                "obbligatorio": True,
            },
        ),
        "pezzi": servizi.salva_campo(capo, m.pk, None, {"etichetta": "Pezzi controllati", "tipo": "INT"}),
        "visivo": servizi.salva_campo(capo, m.pk, None, {"etichetta": "Controllo visivo", "tipo": "ESI"}),
        "colore": servizi.salva_campo(
            capo,
            m.pk,
            None,
            {
                "etichetta": "Colore",
                "tipo": "SCE",
                "scelte": "Bianco\nGrigio\nDifettoso",
                "scelte_non_conformi": "Difettoso",
            },
        ),
        "etichetta": servizi.salva_campo(
            capo, m.pk, None, {"etichetta": "Etichetta presente", "tipo": "BOO", "atteso": "si"}
        ),
        "note": servizi.salva_campo(capo, m.pk, None, {"etichetta": "Descrizione", "tipo": "TXL"}),
        "data": servizi.salva_campo(capo, m.pk, None, {"etichetta": "Data produzione", "tipo": "DAT"}),
        "foto": servizi.salva_campo(capo, m.pk, None, {"etichetta": "Foto", "tipo": "FOT"}),
    }
    return m, campi


@pytest.fixture
def ordine(db):
    o = Ordine.objects.create(serie="00040", numero="0004300", anno=2026)
    RigaOrdine.objects.create(ordine=o, n_riga="1", articolo="18090054BR", qta=Decimal(10))
    Articolo.objects.create(codice="18090054BR")
    return o


def _v(campi, **valori):
    return {f"campo_{campi[k].pk}": v for k, v in valori.items()}


# --------------------------------------------------------------------------------------------- configurazione
def test_configurazione_dei_modelli(capo, collaudo):
    m, campi = collaudo
    assert m.codici == "18090*"  # righe doppie tolte
    assert [c.etichetta for c in m.campi.all()][:2] == ["Coppia", "Pezzi controllati"]
    assert campi["coppia"].minimo == Decimal("1.5") and campi["coppia"].ha_controllo
    assert not campi["pezzi"].ha_controllo and campi["colore"].elenco_non_conformi == ["Difettoso"]
    for dati, errore in [
        ({"etichetta": "X", "tipo": "SCE", "scelte": "una"}, "almeno 2"),
        ({"etichetta": "X", "tipo": "DEC", "minimo": "5", "massimo": "1"}, "minimo non può"),
        ({"etichetta": "X", "tipo": "SCE", "scelte": "a\nb", "scelte_non_conformi": "c"}, "non sono nell'elenco"),
        ({"etichetta": "", "tipo": "TXT"}, "etichetta"),
    ]:
        with pytest.raises(OperazioneNonConsentita, match=errore):
            servizi.salva_campo(capo, m.pk, None, dati)
    servizi.sposta_campo(capo, campi["pezzi"].pk, -1)
    assert m.campi.order_by("ordine").first() == campi["pezzi"]
    with pytest.raises(OperazioneNonConsentita, match="Esiste già"):
        servizi.salva_modello(capo, None, {"nome": "collaudo pulsantiera"})


def test_regole_sui_codici(capo, collaudo):
    m, _ = collaudo
    generico = servizi.salva_modello(capo, None, {"nome": "Nota di produzione", "categoria": "OPE"})
    assert servizi.si_applica(m, ["18090054BR"]) and not servizi.si_applica(m, ["40020025"])
    assert servizi.modelli_per(["18090054br"]) == [m, generico]  # i previsti per primi
    assert servizi.modelli_per(["40020025"]) == [generico]
    assert servizi.modelli_per(["18090054BR"], solo_specifici=True) == [m]


# ---------------------------------------------------------------------------------------------- registrazione
def test_registrazione_ed_esito(capo, collaudo, ordine, media):
    m, campi = collaudo
    foto = SimpleUploadedFile("pezzo.png", PNG, content_type="image/png")
    t = servizi.registra(
        capo,
        m.pk,
        collegamenti={"ordine": ordine.pk, "articolo": "18090054br", "lotto": "L-77", "ddt": "1/2026"},
        valori=_v(
            campi,
            coppia="2,1",
            pezzi="1.200",
            visivo="1",
            colore="Grigio",
            etichetta="1",
            note="Tutto bene",
            data="2026-03-02",
        ),
        files=_v(campi, foto=foto),
    )
    assert t.esito == Esito.CONFORME and t.ordine == ordine and t.lotto == "L-77"
    assert t.ddt is None  # il DDT non è previsto dal modello: ignorato
    valori = {v.campo.etichetta: v for v in t.valori.select_related("campo")}
    assert valori["Coppia"].decimale == Decimal("2.1") and valori["Coppia"].testo_leggibile == "2,1"
    assert valori["Pezzi controllati"].intero == 1200 and valori["Data produzione"].data.day == 2
    assert valori["Foto"].tipo_file == "image/png" and valori["Foto"].file.name.startswith("tracciatura/")
    # un valore fuori dai limiti, un KO o una scelta non conforme: non conforme
    for extra in (
        {"coppia": "3"},
        {"coppia": "2", "visivo": "0"},
        {"coppia": "2", "colore": "Difettoso"},
        {"coppia": "2", "etichetta": "0"},
    ):
        t = servizi.registra(capo, m.pk, collegamenti={"ordine": ordine}, valori=_v(campi, **extra))
        assert t.esito == Esito.NON_CONFORME, extra
    senza = servizi.salva_modello(capo, None, {"nome": "Nota", "categoria": "OPE"})
    servizi.salva_campo(capo, senza.pk, None, {"etichetta": "Testo", "tipo": "TXT"})
    assert servizi.registra(capo, senza.pk, collegamenti={}, valori={}).esito == Esito.SENZA


def test_controlli_in_registrazione(capo, collaudo, ordine, media):
    m, campi = collaudo
    with pytest.raises(OperazioneNonConsentita) as e:
        servizi.registra(
            capo,
            m.pk,
            collegamenti={"articolo": "NONESISTE"},
            valori=_v(campi, coppia="abc", pezzi="1,5", colore="Rosso"),
        )
    testo = str(e.value)
    for frase in (
        "Ordine: obbligatorio",
        "non è nell'anagrafica",
        "Coppia: numero non valido",
        "Pezzi controllati: serve un numero intero",
        "Colore: scelta non valida",
    ):
        assert frase in testo, frase
    with pytest.raises(OperazioneNonConsentita, match="Coppia: obbligatorio"):
        servizi.registra(capo, m.pk, collegamenti={"ordine": ordine}, valori={})
    with pytest.raises(OperazioneNonConsentita, match="serve una fotografia"):
        servizi.registra(
            capo,
            m.pk,
            collegamenti={"ordine": ordine},
            valori=_v(campi, coppia="2"),
            files=_v(campi, foto=SimpleUploadedFile("x.pdf", b"%PDF-1.4 ...", content_type="application/pdf")),
        )
    altro = Ordine.objects.create(serie="00040", numero="9", anno=2026)
    RigaOrdine.objects.create(ordine=altro, n_riga="1", articolo="40020025", qta=Decimal(1))
    with pytest.raises(OperazioneNonConsentita, match="non è previsto per i codici"):
        servizi.registra(capo, m.pk, collegamenti={"ordine": altro}, valori=_v(campi, coppia="2"))
    assert not Tracciatura.objects.exists()


def test_collegamento_a_ddt_componente_e_riferimento(capo):
    m = servizi.salva_modello(
        capo,
        None,
        {
            "nome": "Controllo lotto in ingresso",
            "categoria": "LOT",
            "uso_ordine": "",
            "uso_articolo": "",
            "uso_ddt": "O",
            "uso_componente": "O",
            "uso_riferimento": "F",
        },
    )
    DDT.objects.create(numero="12", anno=2026, data="2026-03-01")
    DDT.objects.create(numero="12", anno=2025, data="2025-03-01")
    Componente.objects.create(codice="22010125")
    with pytest.raises(OperazioneNonConsentita, match="indica anche l'anno"):
        servizi.registra(capo, m.pk, collegamenti={"ddt": "12", "componente": "22010125"}, valori={})
    t = servizi.registra(
        capo, m.pk, collegamenti={"ddt": "12/2026", "componente": "22010125", "riferimento": "SN-1"}, valori={}
    )
    assert t.ddt.anno == 2026 and t.componente == "22010125" and t.riferimento == "SN-1"


def test_campi_usati_non_cambiano_tipo_e_si_disattivano(capo, collaudo, ordine):
    m, campi = collaudo
    servizi.registra(capo, m.pk, collegamenti={"ordine": ordine}, valori=_v(campi, coppia="2", pezzi="5"))
    with pytest.raises(OperazioneNonConsentita, match="il tipo non si cambia"):
        servizi.salva_campo(capo, m.pk, campi["pezzi"].pk, {"etichetta": "Pezzi", "tipo": "TXT"})
    assert servizi.elimina_campo(capo, campi["pezzi"].pk) == "disattivato"
    assert servizi.elimina_campo(capo, campi["note"].pk) == "eliminato"
    assert not Campo.objects.get(pk=campi["pezzi"].pk).attivo


def test_annullamento(capo, collaudo, ordine, operatore):
    m, campi = collaudo
    t = servizi.registra(capo, m.pk, collegamenti={"ordine": ordine}, valori=_v(campi, coppia="2"))
    with pytest.raises(OperazioneNonConsentita, match="Annulla le tue"):
        servizi.annulla(operatore, t.pk, "x")
    mia = servizi.registra(operatore, m.pk, collegamenti={"ordine": ordine}, valori=_v(campi, coppia="2"))
    servizi.annulla(operatore, mia.pk, "doppia")
    servizi.annulla(capo, t.pk, "errore")
    assert not servizi.cerca({}).exists() and servizi.cerca({"annullate": "1"}).count() == 2


# ------------------------------------------------------------------------------------------------------ pagine
def test_pagine(client, capo, collaudo, ordine, media, operatore, cliente):
    m, campi = collaudo
    client.force_login(operatore)
    html = client.get(f"/tracciatura/nuova/?ordine={ordine.pk}").content.decode()
    assert "Collaudo pulsantiera" in html and "previsto" in html
    html = client.get(f"/tracciatura/nuova/?modello={m.pk}&ordine={ordine.pk}").content.decode()
    for frase in (
        'name="campo_',
        'type="file" accept="image/*" capture="environment"',
        ">OK</label>",
        "Difettoso",
        "Accettabile da 1,5 a 2,5 Nm",
        "Verifica a fine linea.",
    ):
        assert frase in html, frase
    r = client.post(
        "/tracciatura/nuova/",
        {
            "modello": m.pk,
            "ordine": ordine.pk,
            "lotto": "L1",
            **_v(campi, coppia="2,2", colore="Bianco"),
            f"campo_{campi['foto'].pk}": SimpleUploadedFile("f.png", PNG, content_type="image/png"),
        },
    )
    t = Tracciatura.objects.get()
    assert r.status_code == 302 and r["Location"] == t.get_absolute_url()
    dettaglio = client.get(t.get_absolute_url()).content.decode()
    assert "conforme" in dettaglio and "2,2 Nm" in dettaglio and "Bianco" in dettaglio
    foto = t.valori.get(campo=campi["foto"])
    assert client.get(f"/tracciatura/file/{foto.pk}/")["Content-Type"] == "image/png"
    assert "Collaudo pulsantiera" in client.get(f"/tracciatura/pannello/?ordine={ordine.pk}").content.decode()
    assert "Collaudo pulsantiera" in client.get("/tracciatura/?codice=18090&esito=OK").content.decode()
    assert "Nessuna tracciatura" in client.get("/tracciatura/?esito=KO").content.decode()
    from openpyxl import load_workbook

    ws = load_workbook(io.BytesIO(client.get(f"/tracciatura/?modello={m.pk}&formato=xlsx").content)).active
    intest = [c.value for c in ws[1]]
    assert "Coppia (Nm)" in intest and ws.cell(2, intest.index("Coppia (Nm)") + 1).value == 2.2
    assert client.get("/tracciatura/modelli/").status_code == 403  # l'operatore non configura
    client.force_login(capo)
    assert "Coppia" in client.get(m.get_absolute_url()).content.decode()
    client.post(m.get_absolute_url(), {"azione": "campo", "etichetta": "Temperatura", "tipo": "DEC", "unita": "°C"})
    assert m.campi.filter(etichetta="Temperatura").exists()
    client.force_login(cliente)
    assert client.get("/tracciatura/").status_code == 403
    assert client.get(f"/tracciatura/file/{foto.pk}/").status_code == 403
