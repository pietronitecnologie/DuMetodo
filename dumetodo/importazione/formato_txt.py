"""Lettura del file ordini TXT esportato dal gestionale del Cliente.

Formato (dalla documentazione di Metodo, vedi docs/analisi-metodo/02-ordini.md §3.1):
- separatore ``@``, prima riga = intestazione, campi riempiti con spazi;
- intestazione in UTF-8, righe dati spesso in Latin-1/CP1252;
- le righe dati hanno spesso un valore in più senza intestazione, es.
  ``701 0004300 00001636`` = magazzino, ordine, codice cliente;
- date ``ggmmaaaa``, numeri con la virgola decimale (``0,2596``).

Differenze rispetto a Metodo: nessuna eccezione non gestita. Ogni riga sbagliata produce un
errore con il numero di riga; la decodifica è robusta (BOM, CRLF, righe vuote finali).
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

SEPARATORE = "@"

COL_ANNO = "Periodo riferimento"
COL_SERIE = "Serie numerazione"
COL_NUMERO = "N.ro Ordine"
COL_APPENDICE = "Appendice"
COL_N_RIGA = "N.ro prog. riga"
COL_SCADENZA = "Data consegna"  # nel file si chiama "consegna" ma è la data di SCADENZA
COL_ARTICOLO = "Cod. Articolo"
COL_VARIANTE = "Variante"
COL_QTA = "Quantita"
COL_PREZZO = "Prezzo finale"
COL_SCONTO = "Sconto unitario"
COL_CLIENTE = "Tipo doc. riferimento cliente"  # presente solo in alcuni file

COLONNE_OBBLIGATORIE = (
    COL_ANNO,
    COL_SERIE,
    COL_NUMERO,
    COL_APPENDICE,
    COL_N_RIGA,
    COL_SCADENZA,
    COL_ARTICOLO,
    COL_VARIANTE,
    COL_QTA,
)


@dataclass(frozen=True)
class ChiaveOrdine:
    serie: str
    numero: str
    appendice: str
    anno: int

    def __str__(self) -> str:
        app = f".{self.appendice}" if self.appendice else ""
        return f"{self.serie}.{self.numero}{app}-{self.anno}"


@dataclass
class RigaFile:
    riga_file: int
    chiave: ChiaveOrdine
    n_riga: str
    articolo: str
    variante: str
    qta: Decimal
    data_scadenza: dt.date
    prezzo: Decimal | None
    sconto: Decimal | None
    cliente: str
    magazzino: str


@dataclass
class ErroreRiga:
    riga_file: int | None
    messaggio: str
    chiave: ChiaveOrdine | None = None


@dataclass
class EsitoLettura:
    righe: list[RigaFile] = field(default_factory=list)
    errori: list[ErroreRiga] = field(default_factory=list)
    errore_file: str = ""

    def per_ordine(self) -> dict[ChiaveOrdine, list[RigaFile]]:
        gruppi: dict[ChiaveOrdine, list[RigaFile]] = {}
        for r in self.righe:
            gruppi.setdefault(r.chiave, []).append(r)
        return gruppi

    @property
    def chiavi_con_errori(self) -> set[ChiaveOrdine]:
        return {e.chiave for e in self.errori if e.chiave is not None}


def _decodifica_riga(raw: bytes) -> str:
    # Decodifica RIGA PER RIGA: nei file reali l'intestazione è UTF-8 e i dati Latin-1/CP1252.
    for codifica in ("utf-8", "cp1252"):
        try:
            return raw.decode(codifica)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")  # non fallisce mai


def _righe(contenuto: bytes) -> list[str]:
    contenuto = contenuto.removeprefix(b"\xef\xbb\xbf")
    return [_decodifica_riga(r) for r in contenuto.splitlines()]


def decimale(testo: str, *, campo: str) -> Decimal:
    t = testo.strip().replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        d = Decimal(t)
    except InvalidOperation:
        raise ValueError(f"{campo} non è un numero: {testo.strip()!r}") from None
    if not d.is_finite():
        raise ValueError(f"{campo} non è un numero: {testo.strip()!r}")
    return d


def _data(testo: str) -> dt.date:
    t = testo.strip()
    if not re.fullmatch(r"\d{8}", t):
        raise ValueError(f"data di consegna non valida: {t!r} (atteso ggmmaaaa)")
    try:
        return dt.datetime.strptime(t, "%d%m%Y").date()
    except ValueError:
        raise ValueError(f"data di consegna inesistente: {t!r}") from None


def normalizza_appendice(valore: str) -> str:
    v = valore.strip().upper()
    return "" if v in ("", "0") else v


def leggi(contenuto: bytes) -> EsitoLettura:
    esito = EsitoLettura()
    righe_testo = _righe(contenuto)
    if not righe_testo or not righe_testo[0].strip():
        esito.errore_file = "Il file è vuoto o non ha l'intestazione."
        return esito
    intestazione = [c.strip() for c in righe_testo[0].split(SEPARATORE)]
    mancanti = [c for c in COLONNE_OBBLIGATORIE if c not in intestazione]
    if mancanti:
        esito.errore_file = f"Intestazione non riconosciuta: mancano le colonne {', '.join(mancanti)}."
        return esito
    idx = {nome: i for i, nome in enumerate(intestazione)}

    for numero, testo in enumerate(righe_testo[1:], start=2):
        if not testo.strip():
            continue
        valori = testo.split(SEPARATORE)
        if len(valori) < len(intestazione):
            esito.errori.append(ErroreRiga(numero, f"la riga ha {len(valori)} campi, ne servono {len(intestazione)}"))
            continue

        def campo(nome: str, valori: list[str] = valori) -> str:
            return valori[idx[nome]].strip() if nome in idx else ""

        chiave = None
        try:
            anno_txt = campo(COL_ANNO)
            if not re.fullmatch(r"\d{4}", anno_txt):
                raise ValueError(f"anno non valido: {anno_txt!r}")
            numero_ordine, serie = campo(COL_NUMERO), campo(COL_SERIE)
            if not numero_ordine or not serie:
                raise ValueError("numero ordine o serie mancante")
            chiave = ChiaveOrdine(serie, numero_ordine, normalizza_appendice(campo(COL_APPENDICE)), int(anno_txt))
            n_riga, articolo = campo(COL_N_RIGA), campo(COL_ARTICOLO)
            if not n_riga or not articolo:
                raise ValueError("numero riga o articolo mancante")
            qta = decimale(campo(COL_QTA), campo="quantità")
            if qta < 0:
                raise ValueError(f"quantità negativa: {qta}")
            prezzo = decimale(campo(COL_PREZZO), campo="prezzo") if campo(COL_PREZZO) else None
            sconto = decimale(campo(COL_SCONTO), campo="sconto") if campo(COL_SCONTO) else None

            magazzino, cliente = "", campo(COL_CLIENTE)
            if not cliente and len(valori) > len(intestazione):
                # Valore extra senza intestazione: "<magazzino> <ordine> <cliente>"
                parti = valori[-1].split()
                if len(parti) >= 3:
                    magazzino, cliente = parti[0], parti[-1]
                elif parti:
                    cliente = parti[-1]
            esito.righe.append(
                RigaFile(
                    riga_file=numero,
                    chiave=chiave,
                    n_riga=n_riga,
                    articolo=articolo,
                    variante=campo(COL_VARIANTE),
                    qta=qta,
                    data_scadenza=_data(campo(COL_SCADENZA)),
                    prezzo=prezzo,
                    sconto=sconto,
                    cliente=cliente[:30],
                    magazzino=magazzino[:20],
                )
            )
        except ValueError as exc:
            esito.errori.append(ErroreRiga(numero, str(exc), chiave))

    # Lo stesso numero di riga due volte nello stesso ordine rende l'ordine ambiguo.
    for chiave, righe in esito.per_ordine().items():
        doppi = [n for n, c in Counter(r.n_riga for r in righe).items() if c > 1]
        for n in doppi:
            esito.errori.append(ErroreRiga(None, f"numero di riga {n} ripetuto nell'ordine", chiave))
    if not esito.righe and not esito.errori:
        esito.errore_file = "Il file non contiene righe d'ordine."
    return esito
