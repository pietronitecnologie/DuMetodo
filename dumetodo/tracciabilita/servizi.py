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
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font

from dumetodo.configurazione import servizi as config
from dumetodo.core.models import Reparto
from dumetodo.core.permessi import F, ha_permesso
from dumetodo.ordini.models import Ordine
from dumetodo.ordini.servizi import OperazioneNonConsentita

from .models import (
    Chiave,
    Controllo,
    ControlloQualita,
    Elemento,
    EsitoControllo,
    FileOrdine,
    Test,
    TipoControllo,
    Traccia,
)

__all__ = ["OperazioneNonConsentita"]


def _richiedi(utente: Any, codice: str = F.TRACCIABILITA_REGISTRA) -> None:
    if not ha_permesso(utente, codice):
        raise OperazioneNonConsentita("Non hai i permessi per questa operazione.")


def _momento(data: dt.date) -> dt.datetime:
    """Data scelta dall'utente -> istante dell'evento: adesso se è oggi, altrimenti mezzanotte (ora locale)."""
    adesso = timezone.localtime()
    if data == adesso.date():
        return adesso
    return timezone.make_aware(dt.datetime.combine(data, dt.time.min))


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


def _utente(utente: Any) -> Any:
    return utente if getattr(utente, "is_authenticated", False) else None


# =========================================================================== tracce generiche
@transaction.atomic
def registra_traccia(
    *,
    utente: Any,
    codice: str,
    chiave: str,
    valore: str,
    data: dt.date,
    reparto: Reparto | None,
    ordine: Ordine | None = None,
) -> Traccia:
    """Evento generico chiave=valore. Se l'elemento esiste già, l'evento gli viene aggiunto."""
    _richiedi(utente)
    codice, chiave, valore = (codice or "").strip(), (chiave or "").strip(), (valore or "").strip()
    if not codice:
        raise OperazioneNonConsentita("Il codice dell'elemento è obbligatorio.")
    if not chiave:
        raise OperazioneNonConsentita("La chiave è obbligatoria.")
    if chiave.upper() in Chiave.RISERVATE:
        raise OperazioneNonConsentita(f"La chiave {chiave} è riservata: usa la pagina dedicata.")
    elemento, _ = Elemento.objects.get_or_create(codice=codice, defaults={"creato_da": _utente(utente)})
    return Traccia.objects.create(
        elemento=elemento,
        ordine=ordine,
        data=_momento(data),
        chiave=chiave,
        valore=valore,
        reparto=reparto,
        inserita_da=_utente(utente),
    )


# =========================================================================== lotto / busta
@transaction.atomic
def registra_lotto_busta(ordine_id: int, lotto: str, busta: str, utente: Any) -> list[Traccia]:
    """Lotto di produzione e busta di una commessa. Un doppio invio dello stesso valore non crea doppioni."""
    _richiedi(utente)
    lotto, busta = (lotto or "").strip(), (busta or "").strip()
    if not lotto and not busta:
        raise OperazioneNonConsentita("Indica il lotto o la busta.")
    if len(lotto) > 100 or len(busta) > 100:
        raise OperazioneNonConsentita("Lotto e busta: massimo 100 caratteri.")
    ordine = Ordine.objects.select_for_update().filter(pk=ordine_id).first()
    if ordine is None:
        raise OperazioneNonConsentita("Ordine inesistente.")
    reparto = Reparto.objects.filter(codice=config.leggi("tracciabilita.reparto_lotti") or "").first()
    adesso = timezone.now()
    create = []
    for chiave, valore in ((Chiave.LOTTO, lotto), (Chiave.BUSTA, busta)):
        if not valore:
            continue
        ultima = ordine.tracce.filter(chiave=chiave).order_by("-data", "-id").first()
        if ultima is not None and ultima.valore == valore:
            continue
        create.append(
            Traccia.objects.create(
                ordine=ordine, data=adesso, chiave=chiave, valore=valore, reparto=reparto, inserita_da=utente
            )
        )
    return create


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


@transaction.atomic
def carica_file_ordine(ordine_id: int, files: Sequence[UploadedFile], utente: Any) -> list[FileOrdine]:
    _richiedi(utente)
    if not files:
        raise OperazioneNonConsentita("Nessun file selezionato.")
    ordine = Ordine.objects.filter(pk=ordine_id).first()
    if ordine is None:
        raise OperazioneNonConsentita("Ordine inesistente.")
    tipi = [valida_file(f) for f in files]  # prima si validano tutti, poi si salva
    caricati = []
    for f, (tipo, estensione) in zip(files, tipi, strict=True):
        fo = FileOrdine(
            ordine=ordine, nome_originale=_nome_originale(f.name), tipo_contenuto=tipo, dimensione=f.size,
            caricato_da=utente,
        )  # fmt: skip
        fo.file.save(f"file{estensione}", f, save=False)
        fo.save()
        caricati.append(fo)
    return caricati


@transaction.atomic
def elimina_file_ordine(file_id: int, utente: Any) -> Ordine:
    fo = FileOrdine.objects.select_for_update().select_related("ordine").filter(pk=file_id).first()
    if fo is None:
        raise OperazioneNonConsentita("File inesistente.")
    if not (
        ha_permesso(utente, F.TRACCIABILITA_FILE_ELIMINA_TUTTI)
        or (ha_permesso(utente, F.TRACCIABILITA_REGISTRA) and fo.caricato_da == utente)
    ):
        raise OperazioneNonConsentita(
            "Può eliminare il file solo chi l'ha caricato o chi ha il permesso di eliminare i file di altri."
        )
    ordine, nome, storage = fo.ordine, fo.file.name, fo.file.storage
    fo.delete()
    transaction.on_commit(lambda: storage.delete(nome))
    return ordine


# =========================================================================== controllo qualità
def controlli_del_test(test: Test) -> list[Controllo]:
    return list(test.controlli.order_by("tipo", "nome"))


def _intero(testo: Any, etichetta: str) -> int:
    t = str(testo if testo is not None else "").strip()
    if not t.isdigit():
        raise OperazioneNonConsentita(f"{etichetta}: indica un numero intero non negativo.")
    return int(t)


@transaction.atomic
def registra_controllo_qualita(
    *,
    utente: Any,
    test: Test,
    articolo: str,
    data: dt.date,
    risultati: Mapping[str, Any],
    ordine: Ordine | None = None,
    riferimento: str = "",
    qta_in_ordine: Decimal | None = None,
) -> ControlloQualita:
    """Registra l'esecuzione di un test. ``risultati`` usa chiavi ``campione_<codice>``, ``ko_<codice>``
    e ``presente_<codice>`` (il codice del controllo, mai la chiave primaria)."""
    _richiedi(utente)
    riferimento, articolo = (riferimento or "").strip(), (articolo or "").strip()
    if not test.attivo:
        raise OperazioneNonConsentita(f"Il test {test} non è attivo.")
    if ordine is None and not riferimento:
        raise OperazioneNonConsentita("Indica l'ordine oppure il riferimento di stock.")
    if not articolo:
        raise OperazioneNonConsentita("Il codice prodotto è obbligatorio.")
    controlli = controlli_del_test(test)
    if not controlli:
        raise OperazioneNonConsentita(f"Il test {test} non ha controlli configurati.")
    esiti = []
    for c in controlli:
        if c.tipo == TipoControllo.PRESENZA:
            presente = str(risultati.get(f"presente_{c.codice}", "")).lower() in ("on", "1", "true", "si", "sì")
            esiti.append(EsitoControllo(controllo=c, presente=presente))
            continue
        campione = _intero(risultati.get(f"campione_{c.codice}"), f"{c.nome} – campione")
        ko = _intero(risultati.get(f"ko_{c.codice}"), f"{c.nome} – KO")
        if ko > campione:
            raise OperazioneNonConsentita(f"{c.nome}: i KO ({ko}) superano il campione ({campione}).")
        esiti.append(EsitoControllo(controllo=c, campione=campione, ko=ko))
    cq = ControlloQualita.objects.create(
        ordine=ordine, riferimento=riferimento if ordine is None else "", articolo=articolo,
        qta_in_ordine=qta_in_ordine, test=test, data=data, eseguito_da=utente,
    )  # fmt: skip
    for e in esiti:
        e.controllo_qualita = cq
    EsitoControllo.objects.bulk_create(esiti)
    return cq


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


def esporta_qualita(qs: QuerySet[EsitoControllo]) -> bytes:
    righe = (
        (
            e.controllo_qualita.soggetto,
            e.controllo_qualita.articolo,
            e.controllo_qualita.qta_in_ordine,
            e.controllo_qualita.test.nome,
            e.controllo.nome,
            e.controllo_qualita.data,
            e.campione,
            e.ko,
            "" if e.presente is None else ("Presente" if e.presente else "Assente"),
            str(e.controllo_qualita.eseguito_da or ""),
        )
        for e in qs
    )
    intestazioni = (
        "Ordine/Stock", "Codice prodotto", "Qtà in ordine", "Test", "Controllo", "Data", "Campione", "KO",
        "Presenza", "Eseguito da",
    )  # fmt: skip
    return xlsx("Controlli qualità", intestazioni, righe, {5: DATA})


def esporta_file(qs: QuerySet[FileOrdine]) -> bytes:
    righe = ((f.ordine.codice_breve, f.caricato_il, f.nome_originale, f.tipo_contenuto, f.dimensione) for f in qs)
    return xlsx("File", ("Ordine", "Caricato il", "Nome", "Tipo", "Byte"), righe, {1: DATA_ORA})
