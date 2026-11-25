"""Tracciati predefiniti e dati di esempio per l'anteprima in Configurazione."""

import datetime as dt
from decimal import Decimal

# Riproduce il file di importazione bolle generato da Metodo per ReadyPro
# (docs/analisi-metodo/02-ordini.md §4.12).
READYPRO_BOLLA = """\
# Tracciato bolla per il gestionale ReadyPro (stesso formato generato da Metodo).
# Una riga "Riferimento" per ogni ordine, poi una riga per ogni riga consegnata:
#   articolo[.variante senza _]||quantità intera|PZ|prezzo con la virgola
nome: ReadyPro
estensione: txt
codifica: cp1252
fine_riga: CRLF
nome_file: consegna{documento.selezione}-{documento.oggi|data:AAAAMMGG}

[ordine]
Riferimento |ordine {ordine.serie}.{ordine.numero}

[riga]
{riga.articolo}{riga.variante|senza:_|prefisso:.}||{riga.qta|intero}|PZ|{riga.prezzo|compatto|virgola}
"""

# File tecnico DDT per il cliente (stesso formato di Metodo, ddt.py). Correzioni rispetto a Metodo:
# quantità 100,05 scritta "100,05" (Metodo scriveva "1005") e anno del nome file = anno del DDT.
FILE_TECNICO_DDT = """\
# File tecnico DDT (stesso formato generato da Metodo).
# Primo record "T+R", i successivi "RIG"; 3649 è il codice del Fornitore presso il Cliente.
nome: File tecnico DDT
estensione: txt
codifica: cp1252
fine_riga: CRLF
nome_file: DDT_3649_{documento.data|data:AAAA}_{documento.numero}

[riga]
{riga.progressivo_file|uguale:1,T+R,RIG}@3649@{documento.data|data:GG/MM/AAAA}@{documento.numero}@{riga.articolo}@{riga.variante}@{riga.qta|compatto|virgola}@{ordine.anno}.{ordine.serie|senza_zeri}.{ordine.numero|senza_zeri}{ordine.appendice|prefisso:.}.{riga.n_riga|senza_zeri},00@1
"""

DOCUMENTO_ESEMPIO = {
    "oggi": dt.date(2026, 4, 2),
    "ora": dt.datetime(2026, 4, 2, 15, 30),
    "selezione": "40",
    "utente": "Mario Rossi",
    "numero": "123",
    "data": dt.date(2026, 4, 2),
}

ORDINI_ESEMPIO = [
    (
        {
            "serie": "00040",
            "numero": "0004300",
            "appendice": "",
            "anno": 2026,
            "codice": "00040.0004300-2026",
            "codice_breve": "40.0004300-2026",
            "cliente": "00001636",
            "data_scadenza": dt.date(2026, 4, 2),
        },
        [
            {
                "n_riga": "0000001",
                "articolo": "18090054BR",
                "variante": "__0",
                "qta": Decimal("2.000"),
                "prezzo": Decimal("0.2596"),
                "sconto": None,
                "importo": Decimal("0.52"),
                "cliente": "00001636",
                "magazzino": "701",
                "data_scadenza": dt.date(2026, 4, 2),
            },
            {
                "n_riga": "0000007",
                "articolo": "18150039",
                "variante": "",
                "qta": Decimal("4.000"),
                "prezzo": Decimal("12.5000"),
                "sconto": None,
                "importo": Decimal("50.00"),
                "cliente": "00001636",
                "magazzino": "701",
                "data_scadenza": dt.date(2026, 4, 2),
            },
        ],
    ),
    (
        {
            "serie": "00040",
            "numero": "0004301",
            "appendice": "T1",
            "anno": 2026,
            "codice": "00040.0004301.T1-2026",
            "codice_breve": "40.0004301-T1-2026",
            "cliente": "00001636",
            "data_scadenza": dt.date(2026, 4, 9),
        },
        [
            {
                "n_riga": "0000001",
                "articolo": "CAVO-3X1",
                "variante": "_-1",
                "qta": Decimal("12.500"),
                "prezzo": Decimal("1.1000"),
                "sconto": None,
                "importo": Decimal("13.75"),
                "cliente": "00001636",
                "magazzino": "711",
                "data_scadenza": dt.date(2026, 4, 9),
            },
        ],
    ),
]
