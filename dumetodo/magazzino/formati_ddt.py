"""Lettura dei file dei DDT in ingresso esportati dal gestionale (CSV/TXT e XML).

Funzioni pure: ricevono i byte del file e restituiscono le righe lette e quelle scartate, senza
toccare il database. Il file è letto solo in memoria (la dimensione massima la controlla chi chiama).

Correzioni rispetto a Metodo (docs/analisi-metodo/03-magazzino.md §7):
- CSV: accetta UTF-8 con o senza BOM e Windows-1252 (Metodo solo UTF-8, e con il BOM perdeva il
  numero documento di tutte le righe); quantità con separatore delle migliaia ("1.000,5");
  una data non valida scarta la riga invece di diventare "oggi" in silenzio;
- quantità zero o negative scartate sia nel CSV sia nell'XML (prima il CSV le accettava);
- XML: rifiutati DTD ed entità (protezione da "billion laughs" e simili), filtro facoltativo sul
  tipo documento (``<DocType>``), riga del documento conservata per riconoscere le reimportazioni;
- codici normalizzati (maiuscolo) e testi troncati alla lunghezza dei campi invece di far fallire
  l'intera importazione.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .models import normalizza_codice

MAX_CODICE = 50
MAX_NUMERO = 50
MAX_FORNITORE = 100
MAX_DESCRIZIONE = 200
MAX_UM = 10


class ErroreFormato(Exception):
    """Il file non è nel formato atteso: nessuna riga viene importata."""


@dataclass(frozen=True)
class RigaDDT:
    numero: str
    data: dt.date
    fornitore: str
    codice: str
    descrizione: str
    unita_misura: str
    quantita: Decimal
    prezzo_unitario: Decimal | None
    riga_documento: int

    @property
    def chiave_ddt(self) -> tuple[str, str, dt.date]:
        return (self.fornitore, self.numero, self.data)


@dataclass
class Lettura:
    righe: list[RigaDDT] = field(default_factory=list)
    scarti: list[str] = field(default_factory=list)


def numero_decimale(testo: str | None) -> Decimal | None:
    """Numero in formato italiano ("1.000,50") o con il punto decimale ("1000.50")."""
    t = (testo or "").strip().replace(" ", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _decodifica(contenuto: bytes) -> str:
    if contenuto.startswith(b"\xef\xbb\xbf"):
        return contenuto[3:].decode("utf-8")
    try:
        return contenuto.decode("utf-8")
    except UnicodeDecodeError:
        return contenuto.decode("cp1252", errors="replace")


def _nome_colonna(nome: str | None) -> str:
    t = (nome or "").strip().strip('"').lower()
    for a, b in (("à", "a"), ("'", ""), ("’", ""), ("`", "")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t)


COLONNE_CSV = {
    "numero documento e appendice": "numero",
    "data": "data",
    "ragione sociale": "fornitore",
    "codice articolo": "codice",
    "descrizione articolo/conto": "descrizione",
    "unita di misura": "um",
    "quantita": "quantita",
}


def _conteggio_riga(contatori: dict[tuple[str, str, dt.date], int], chiave: tuple[str, str, dt.date]) -> int:
    contatori[chiave] = contatori.get(chiave, 0) + 1
    return contatori[chiave]


def leggi_csv(contenuto: bytes) -> Lettura:
    """Esportazione del gestionale separata da ``;``, una riga per materiale (spec §7.3)."""
    testo = _decodifica(contenuto)
    lettore = csv.reader(io.StringIO(testo, newline=""), delimiter=";", quotechar='"')
    try:
        intestazione = next(lettore)
    except StopIteration:
        raise ErroreFormato("Il file è vuoto.") from None
    except csv.Error as exc:
        raise ErroreFormato(f"File CSV non leggibile: {exc}") from exc
    indici = {COLONNE_CSV[n]: i for i, n in enumerate(map(_nome_colonna, intestazione)) if n in COLONNE_CSV}
    mancanti = {"numero", "data", "codice", "quantita"} - indici.keys()
    if mancanti:
        raise ErroreFormato(
            "Intestazione non riconosciuta: mancano le colonne "
            + ", ".join(sorted(k for k, v in COLONNE_CSV.items() if v in mancanti))
            + ". La prima riga deve contenere i nomi delle colonne del gestionale."
        )
    esito = Lettura()
    contatori: dict[tuple[str, str, dt.date], int] = {}
    try:
        for valori in lettore:
            n = lettore.line_num
            if not any(v.strip() for v in valori):
                continue

            def campo(nome: str, valori: list[str] = valori) -> str:
                i = indici.get(nome)
                return valori[i].strip() if i is not None and i < len(valori) else ""

            codice = normalizza_codice(campo("codice"))
            numero = campo("numero")
            try:
                data = dt.datetime.strptime(campo("data"), "%d/%m/%Y").date()
            except ValueError:
                esito.scarti.append(f"Riga {n}: data {campo('data')!r} non valida (atteso gg/mm/aaaa).")
                continue
            fornitore = campo("fornitore")[:MAX_FORNITORE]
            if not numero:
                esito.scarti.append(f"Riga {n}: numero documento mancante.")
                continue
            if len(numero) > MAX_NUMERO:
                esito.scarti.append(f"Riga {n}: numero documento troppo lungo.")
                continue
            posizione = _conteggio_riga(contatori, (fornitore, numero, data))
            errore = _controlla_codice_quantita(codice, campo("quantita"))
            if errore:
                esito.scarti.append(f"Riga {n} ({codice or 'senza codice'}): {errore}")
                continue
            esito.righe.append(
                RigaDDT(
                    numero=numero,
                    data=data,
                    fornitore=fornitore,
                    codice=codice,
                    descrizione=campo("descrizione")[:MAX_DESCRIZIONE],
                    unita_misura=(campo("um") or "EA")[:MAX_UM],
                    quantita=numero_decimale(campo("quantita")),  # type: ignore[arg-type]
                    prezzo_unitario=None,
                    riga_documento=posizione,
                )
            )
    except csv.Error as exc:
        raise ErroreFormato(f"File CSV non leggibile alla riga {lettore.line_num}: {exc}") from exc
    return esito


def _controlla_codice_quantita(codice: str, quantita: str | None) -> str:
    if not codice:
        return "codice articolo mancante."
    if len(codice) > MAX_CODICE:
        return f"codice articolo più lungo di {MAX_CODICE} caratteri."
    q = numero_decimale(quantita)
    if q is None:
        return f"quantità {quantita!r} non valida."
    if q <= 0:
        return "quantità zero o negativa."
    if q >= Decimal(10) ** 11 or q != q.quantize(Decimal("0.001")):
        return f"quantità {quantita!r} fuori formato (massimo 3 decimali)."
    return ""


# ----------------------------------------------------------------------------------------- XML
_VIETATI = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def _testo(elemento: ET.Element | None, percorso: str) -> str:
    if elemento is None:
        return ""
    trovato = elemento.find(percorso)
    return (trovato.text or "").strip() if trovato is not None else ""


def analizza_xml_sicuro(contenuto: bytes) -> ET.Element:
    """Parsing di XML non fidato: niente DTD, niente entità, niente codifiche multi-byte esotiche."""
    if contenuto.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in contenuto:
        raise ErroreFormato("Codifica del file XML non supportata (usa UTF-8 o Windows-1252).")
    if _VIETATI.search(contenuto):
        raise ErroreFormato("Il file XML contiene una DTD o delle entità: non è ammesso.")
    try:
        return ET.fromstring(contenuto)  # noqa: S314
    except ET.ParseError as exc:
        raise ErroreFormato(f"File XML non valido: {exc}") from exc


def leggi_xml(contenuto: bytes, tipi_documento: Iterable[str] = ()) -> Lettura:
    """Formato proprietario del gestionale ``<Docs><Doc>…`` (non FatturaPA, spec §7.4)."""
    radice = analizza_xml_sicuro(contenuto)
    if radice.tag != "Docs":
        raise ErroreFormato(f"Elemento radice '{radice.tag}' non valido: atteso 'Docs'.")
    tipi = {t.strip() for t in tipi_documento if t.strip()}
    esito = Lettura()
    for i, doc in enumerate(radice.findall("Doc"), start=1):
        numero = _testo(doc, "DocNumber")
        rif = f"Documento {i}" + (f" (n. {numero})" if numero else "")
        tipo = _testo(doc, "DocType")
        if tipi and tipo not in tipi:
            esito.scarti.append(f"{rif}: tipo documento {tipo or '(vuoto)'} non accettato.")
            continue
        if not numero or len(numero) > MAX_NUMERO:
            esito.scarti.append(f"{rif}: numero documento mancante o troppo lungo.")
            continue
        try:
            data = dt.date.fromisoformat(_testo(doc, "DocDate"))
        except ValueError:
            esito.scarti.append(f"{rif}: data {_testo(doc, 'DocDate')!r} non valida (atteso AAAA-MM-GG).")
            continue
        fornitore = _testo(doc, "Anag/Name")[:MAX_FORNITORE]
        righe = doc.find("DocRows")
        if righe is None:
            esito.scarti.append(f"{rif}: nessuna riga trovata nel documento.")
            continue
        for j, riga in enumerate(righe.findall("DocRow"), start=1):
            codice = normalizza_codice(_testo(riga, "Product/Code"))
            if not codice and riga.find("Product") is None:
                esito.scarti.append(f"{rif}, riga {j}: riga senza articolo (descrittiva), ignorata.")
                continue
            errore = _controlla_codice_quantita(codice, _testo(riga, "Quantity"))
            if errore:
                esito.scarti.append(f"{rif}, riga {j} ({codice or 'senza codice'}): {errore}")
                continue
            prezzo = numero_decimale(_testo(riga, "Price"))
            if prezzo is not None and (prezzo < 0 or prezzo >= Decimal(10) ** 10):
                prezzo = None
            esito.righe.append(
                RigaDDT(
                    numero=numero,
                    data=data,
                    fornitore=fornitore,
                    codice=codice,
                    descrizione=_testo(riga, "Description")[:MAX_DESCRIZIONE],
                    unita_misura="EA",
                    quantita=numero_decimale(_testo(riga, "Quantity")),  # type: ignore[arg-type]
                    prezzo_unitario=prezzo.quantize(Decimal("0.0001")) if prezzo is not None else None,
                    riga_documento=j,
                )
            )
    if not radice.findall("Doc"):
        raise ErroreFormato("Nessun documento <Doc> nel file.")
    return esito
