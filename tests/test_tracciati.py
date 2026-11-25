import datetime as dt
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from metodo.configurazione import servizi as config
from metodo.tracciati import esempi
from metodo.tracciati.linguaggio import ErroreTracciato, compila, genera


def _genera(sorgente, ordini=None, documento=None):
    return genera(compila(sorgente), documento or esempi.DOCUMENTO_ESEMPIO, ordini or esempi.ORDINI_ESEMPIO)


def test_readypro_identico_a_metodo():
    f = _genera(esempi.READYPRO_BOLLA)
    assert f.nome == "consegna40-20260402.txt"
    assert f.contenuto == (
        b"Riferimento |ordine 00040.0004300\r\n"
        b"18090054BR.0||2|PZ|0,2596\r\n"  # variante "__0" -> ".0", quantità intera, prezzo con la virgola
        b"18150039||4|PZ|12,5\r\n"  # variante vuota: niente punto
        b"Riferimento |ordine 00040.0004301\r\n"
        b"CAVO-3X1.-1||12|PZ|1,1\r\n"  # 12,5 metri -> 12 come in Metodo (filtro "intero")
    )


def test_tutte_le_sezioni_e_contatori():
    sorgente = """
fine_riga: LF
codifica: utf-8
nome_file: export
[intestazione]
INIZIO {documento.numero_ordini} ordini {documento.numero_righe} righe {documento.ora|data:GG.MM.AA hh:mm}
[ordine]
O{ordine.progressivo} {ordine.numero|senza_zeri}{ordine.appendice|prefisso:-}
[riga]
  {riga.progressivo}/{riga.progressivo_file} {riga.qta|arrotonda:2|virgola|sinistra:8,0} {riga.importo|predefinito:"-"}
[fine_ordine]
TOT {ordine.totale_qta|compatto} su {ordine.numero_righe}
[chiusura]
FINE {{fisso}} {"#"}
"""
    f = _genera(sorgente)
    assert f.nome == "export.txt"
    assert f.testo.splitlines() == [
        "INIZIO 2 ordini 3 righe 02.04.26 15:30",
        "O1 4300",
        "  1/1 00002,00 0.52",
        "  2/2 00004,00 50.00",
        "TOT 6 su 2",
        "O2 4301-T1",
        "  1/3 00012,50 13.75",
        "TOT 12.5 su 1",
        "FINE {fisso} #",
    ]


@pytest.mark.parametrize(
    "sorgente,messaggio",
    [
        ("[riga]\n{riga.inesistente}", "campo sconosciuto"),
        ("[riga]\n{articolo}", "gruppo.campo"),
        ("[ordine]\n{riga.articolo}\n[riga]\nx", "non è disponibile"),
        ("[riga]\n{riga.articolo|boh}", "filtro sconosciuto"),
        ("[riga]\n{riga.articolo|intero}", "non si applica"),
        ("[riga]\n{riga.qta|data:GG}", "non si applica"),
        ("[riga]\n{riga.articolo|tronca}", "argomenti"),
        ("[riga]\n{riga.articolo|tronca:x}", "intero"),
        ("[riga]\n{riga.articolo", "non chiusa"),
        ("[riga]\nabc }", "senza apertura"),
        ("[righe]\nx", "sezione sconosciuta"),
        ("codifica: ebcdic\n[riga]\nx", "codifica"),
        ("colore: rosso\n[riga]\nx", "impostazione non valida"),
        ("[ordine]\nx", "manca la sezione [riga]"),
        ("[riga]\nx\n[riga]\ny", "ripetuta"),
    ],
)
def test_errori_segnalati_con_riga(sorgente, messaggio):
    with pytest.raises(ErroreTracciato) as exc:
        compila(sorgente)
    assert messaggio in str(exc.value)


def test_numero_di_riga_nell_errore():
    with pytest.raises(ErroreTracciato) as exc:
        compila("nome: x\n\n[riga]\n{riga.articolo}\n{riga.boh}")
    assert exc.value.riga == 5


def test_carattere_non_rappresentabile_nella_codifica():
    t = compila("codifica: ascii\n[riga]\n{riga.articolo}")
    righe = [({"numero": "1"}, [{"articolo": "ARTÈ", "qta": Decimal(1)}])]
    with pytest.raises(ErroreTracciato, match="non è rappresentabile"):
        genera(t, {"oggi": dt.date.today()}, righe)


def test_nome_file_ripulito():
    f = _genera("nome_file: ../../{documento.utente}\n[riga]\nx")
    assert f.nome == "_.._Mario Rossi.txt"


@pytest.mark.django_db
def test_parametro_predefinito_e_validazione():
    assert config.leggi("consegna.tracciato_bolla") == esempi.READYPRO_BOLLA
    with pytest.raises(ValidationError, match="Riga 2"):
        config.scrivi("consegna.tracciato_bolla", "[riga]\n{riga.nulla}")
    config.scrivi("consegna.tracciato_bolla", "[riga]\r\n{riga.articolo}")
    config.invalida_cache()
    assert config.leggi("consegna.tracciato_bolla") == "[riga]\n{riga.articolo}"


@pytest.mark.django_db
def test_anteprima_e_guida(client, amministratore, operatore):
    client.force_login(amministratore)
    r = client.post(
        "/configurazione/tracciati/anteprima/",
        {"campo": "consegna-consegna.tracciato_bolla", "consegna-consegna.tracciato_bolla": esempi.READYPRO_BOLLA},
    )
    assert b"consegna40-20260402.txt" in r.content and b"18090054BR.0||2|PZ|0,2596" in r.content
    r = client.post(
        "/configurazione/tracciati/anteprima/",
        {"campo": "consegna-consegna.tracciato_bolla", "consegna-consegna.tracciato_bolla": "[riga]\n{x.y}"},
    )
    assert b"campo sconosciuto" in r.content
    assert client.get("/configurazione/tracciati/guida/").status_code == 200
    assert b"Ripristina predefinito" in client.get("/configurazione/").content
    client.force_login(operatore)
    assert client.post("/configurazione/tracciati/anteprima/", {}).status_code == 403
