"""Funzioni condivise rimaste dalla tracciabilità di Metodo (ora tutto passa dalle Tracciature).

- ordini scritti come testo: si trovano per chiave completa (serie, numero, appendice, anno), mai con
  ``contains`` sul solo numero come in Metodo (usate dall'importazione da Metodo);
- controllo dei file caricati (tipo verificato dal contenuto, dimensione massima): usato dalle Tracciature.
"""

from __future__ import annotations

import re
from pathlib import PurePath

from django.core.files.uploadedfile import UploadedFile

from metodo.configurazione import servizi as config
from metodo.ordini.models import Ordine
from metodo.ordini.servizi import OperazioneNonConsentita

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
