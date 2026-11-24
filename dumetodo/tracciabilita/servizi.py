"""Logica di business di tracciabilità e qualità. Le viste chiamano SOLO queste funzioni.

Come negli altri moduli ``servizi``: ogni operazione gira in una transazione, blocca le righe che
modifica, verifica i permessi lato server e solleva ``OperazioneNonConsentita`` con un messaggio
leggibile. Difetti di Metodo corretti qui (vedi docs/analisi-metodo/04, §9):
- nessun ordine viene più creato implicitamente: un ordine sconosciuto è un errore;
- l'ordine si trova per chiave completa (serie, numero, appendice, anno), mai con ``contains``;
- il valore delle tracce generiche viene salvato; a un elemento esistente si possono aggiungere eventi;
"""

from __future__ import annotations

import datetime as dt
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from pathlib import PurePath
from typing import Any

from django.core.files.uploadedfile import UploadedFile
from django.db.models import QuerySet
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font

from dumetodo.configurazione import servizi as config
from dumetodo.ordini.models import Ordine
from dumetodo.ordini.servizi import OperazioneNonConsentita

from .models import (
    Traccia,
)

__all__ = ["OperazioneNonConsentita"]


# =========================================================================== ordini
FORMATO_RE = re.compile(
    r"^(?P<serie>\d{1,5})\.(?P<numero>\d{1,30})(?:[.\-/](?P<appendice>[A-Za-z0-9]{1,10}))?[.\-/](?P<anno>\d{4})$"
)
FORMATO_ORDINE = "serie.numero[.appendice]-anno, es. 40.0004300-2026 o 00040.0004300.T1-2026"


def cerca_ordine(serie: str, numero: str, appendice: str, anno: int | str) -> Ordine | None:
    """Ordine per chiave completa. Serie e numero accettano anche la forma senza zeri iniziali."""
    serie, numero = str(serie).strip(), str(numero).strip()
    appendice = str(appendice or "").strip().upper()
    if appendice in ("0", "NONE", "-"):
        appendice = ""
    try:
        anno = int(anno)
    except (TypeError, ValueError):
        return None
    if not serie or not numero:
        return None
    numeri = {numero, numero.zfill(7)} if numero.isdigit() else {numero}
    serie_possibili = {serie, serie.zfill(5)} if serie.isdigit() else {serie}
    return (
        Ordine.objects.filter(serie__in=serie_possibili, numero__in=numeri, appendice=appendice, anno=anno)
        .order_by("pk")
        .first()
    )


def risolvi_ordine(testo: str) -> Ordine:
    """Trova l'ordine dal codice scritto dall'utente o inviato da un banco. Solleva se non esiste."""
    testo = (testo or "").strip()
    m = FORMATO_RE.match(testo)
    if not m:
        raise OperazioneNonConsentita(f"Codice ordine non valido: {testo!r}. Formato: {FORMATO_ORDINE}.")
    ordine = cerca_ordine(m["serie"], m["numero"], m["appendice"] or "", m["anno"])
    if ordine is None:
        raise OperazioneNonConsentita(f"Ordine {testo} non trovato.")
    return ordine


# =========================================================================== file
_FIRME: tuple[tuple[bytes, str, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    (b"%PDF-", "application/pdf", ".pdf"),
)
TIPI_AMMESSI = "immagini JPEG, PNG, GIF, WebP o documenti PDF"


def riconosci_file(f: UploadedFile) -> tuple[str, str]:
    """Tipo reale del file dai primi byte (non ci si fida né del nome né del Content-Type del browser)."""
    f.seek(0)
    testa = f.read(16)
    f.seek(0)
    if testa[:4] == b"RIFF" and testa[8:12] == b"WEBP":
        return "image/webp", ".webp"
    for firma, tipo, estensione in _FIRME:
        if testa.startswith(firma):
            return tipo, estensione
    raise OperazioneNonConsentita(f"Tipo di file non ammesso: {f.name}. Sono ammessi {TIPI_AMMESSI}.")


def valida_file(f: UploadedFile) -> tuple[str, str]:
    massimo_mb = config.leggi("tracciabilita.dimensione_massima_file_mb")
    if not f.size:
        raise OperazioneNonConsentita(f"Il file {f.name} è vuoto.")
    if f.size > massimo_mb * 1024 * 1024:
        raise OperazioneNonConsentita(f"Il file {f.name} supera la dimensione massima di {massimo_mb} MB.")
    return riconosci_file(f)


def _nome_originale(nome: str | None) -> str:
    return PurePath((nome or "file").replace("\\", "/")).name[-200:] or "file"


# =========================================================================== esportazioni xlsx
def _cella(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return timezone.localtime(v).replace(tzinfo=None) if timezone.is_aware(v) else v
    if isinstance(v, Decimal):
        return float(v)
    return v


def xlsx(titolo: str, intestazioni: Sequence[str], righe: Iterable[Sequence[Any]], formati: Mapping[int, str]) -> bytes:
    """Foglio xlsx semplice. ``formati``: indice colonna (0-based) -> formato numerico (es. date)."""
    wb = Workbook()
    ws = wb.active
    ws.title = titolo[:31]
    ws.append(["N", *intestazioni])
    for c in ws[1]:
        c.font = Font(bold=True)
    for n, riga in enumerate(righe, start=1):
        ws.append([n, *(_cella(v) for v in riga)])
        for indice, formato in formati.items():
            ws.cell(row=n + 1, column=indice + 2).number_format = formato
    ws.freeze_panes = "A2"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


DATA = "dd/mm/yyyy"
DATA_ORA = "dd/mm/yyyy hh:mm"


def _ordine_testo(o: Ordine | None) -> str:
    return o.codice_breve if o else ""


def esporta_tracce(qs: QuerySet[Traccia]) -> bytes:
    righe = (
        (
            t.elemento.codice if t.elemento else "",
            _ordine_testo(t.ordine),
            t.inserita_il,
            t.data,
            t.chiave,
            t.valore,
            str(t.reparto or ""),
        )
        for t in qs
    )
    intestazioni = ("Codice", "Ordine", "Data inserimento", "Data", "Chiave", "Valore", "Reparto")
    return xlsx("Tracce", intestazioni, righe, {2: DATA_ORA, 3: DATA_ORA})
