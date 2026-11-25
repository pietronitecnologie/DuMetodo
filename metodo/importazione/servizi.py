"""Applicazione di un file d'ordini al database.

Regole (evoluzione di ``insertLinea`` di Metodo, vedi docs/analisi-metodo/02-ordini.md §3.5):

1. Il file è conservato com'è (``FileImportato``); un file identico già importato viene saltato.
2. Ogni ordine del file è una FOTOGRAFIA COMPLETA dell'ordine ed è applicato in una transazione
   propria: o tutto l'ordine o niente. Un ordine con anche una sola riga illeggibile NON viene
   applicato (altrimenti le righe mancanti verrebbero annullate per errore).
3. Ordine consegnato -> reimportazione rifiutata e segnalata (come in Metodo).
4. Confronto per numero di riga:
   - riga nuova -> creata;
   - stesso numero, stesso articolo/variante -> aggiornate quantità, prezzo, scadenza, cliente;
   - stesso numero, articolo diverso -> vecchia riga "sostituita", nuova creata (ex "duplicata");
   - riga non più presente nel file -> annullata.
   In Metodo invece si annullavano TUTTE le righe inserite prima di oggi, comprese quelle già
   in bolla, e si ricreavano da zero perdendo blocchi, evasioni e divisioni.
5. Righe già in bolla: mai modificate; le differenze sono segnalate come avvisi.
6. Righe divise: la quantità del file è il totale; la riga originale tiene la differenza.
7. Ogni variazione di scadenza finisce nello storico scadenze.
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.template.loader import render_to_string
from django.utils import timezone

from metodo.comunicazioni import email
from metodo.configurazione import servizi as config
from metodo.ordini.models import (
    Ordine,
    OrigineModificaScadenza,
    OrigineRiga,
    RigaOrdine,
    StatoOrdine,
    StatoRiga,
)
from metodo.ordini.servizi import registra_scadenza

from . import formato_txt
from .formato_txt import ChiaveOrdine, RigaFile
from .models import FileImportato, Livello, OrigineFile, ProblemaImportazione, StatoFile

logger = logging.getLogger(__name__)


@dataclass
class _Conteggi:
    ordini_creati: int = 0
    ordini_aggiornati: int = 0
    ordini_scartati: int = 0
    righe_create: int = 0
    righe_aggiornate: int = 0
    righe_annullate: int = 0
    problemi: list[ProblemaImportazione] = field(default_factory=list)


def registra_file(
    nome: str, contenuto: bytes, origine: str, *, utente: Any = None, messaggio: Any = None
) -> FileImportato:
    return FileImportato.objects.create(
        nome=nome[:255],
        contenuto=contenuto,
        sha256=hashlib.sha256(contenuto).hexdigest(),
        origine=origine,
        caricato_da=utente if getattr(utente, "is_authenticated", False) else None,
        messaggio=messaggio,
    )


def importa(file: FileImportato, *, forza: bool = False) -> FileImportato:
    """Elabora un ``FileImportato``. Ritorna il file aggiornato con stato e conteggi."""
    gia_importato = (
        FileImportato.objects.filter(sha256=file.sha256, stato__in=[StatoFile.IMPORTATO, StatoFile.CON_AVVISI])
        .exclude(pk=file.pk)
        .exists()
    )
    if gia_importato and not forza:
        return _chiudi(file, StatoFile.DUPLICATO, _Conteggi(), righe_lette=0)

    lettura = formato_txt.leggi(bytes(file.contenuto))
    c = _Conteggi()
    if lettura.errore_file:
        c.problemi.append(ProblemaImportazione(file=file, livello=Livello.ERRORE, messaggio=lettura.errore_file))
        return _chiudi(file, StatoFile.NON_VALIDO, c, righe_lette=0)

    for err in lettura.errori:
        c.problemi.append(
            ProblemaImportazione(
                file=file,
                livello=Livello.ERRORE,
                riga_file=err.riga_file,
                chiave_ordine=str(err.chiave or ""),
                messaggio=f"Riga {err.riga_file}: {err.messaggio}" if err.riga_file else err.messaggio,
            )
        )
    righe_senza_ordine = any(e.chiave is None for e in lettura.errori)
    da_scartare = lettura.chiavi_con_errori

    for chiave, righe in lettura.per_ordine().items():
        if righe_senza_ordine or chiave in da_scartare:
            c.ordini_scartati += 1
            c.problemi.append(
                ProblemaImportazione(
                    file=file,
                    livello=Livello.ERRORE,
                    chiave_ordine=str(chiave),
                    messaggio=f"Ordine {chiave} non importato: il file contiene righe non leggibili. "
                    "Correggi il file e ricaricalo.",
                )
            )
            continue
        try:
            with transaction.atomic():
                _applica_ordine(file, chiave, righe, c)
        except Exception as exc:  # un ordine non deve bloccare gli altri
            logger.exception("Errore importando l'ordine %s dal file %s", chiave, file.pk)
            c.ordini_scartati += 1
            c.problemi.append(
                ProblemaImportazione(
                    file=file,
                    livello=Livello.ERRORE,
                    chiave_ordine=str(chiave),
                    messaggio=f"Ordine {chiave} non importato per un errore imprevisto: {type(exc).__name__}: {exc}",
                )
            )

    from metodo.anagrafica.servizi import registra_articoli

    registra_articoli(r.articolo for r in lettura.righe)
    if any(p.livello == Livello.ERRORE for p in c.problemi):
        stato = StatoFile.CON_ERRORI
    elif c.problemi:
        stato = StatoFile.CON_AVVISI
    else:
        stato = StatoFile.IMPORTATO
    return _chiudi(file, stato, c, righe_lette=len(lettura.righe) + len(lettura.errori))


def _chiudi(file: FileImportato, stato: str, c: _Conteggi, *, righe_lette: int) -> FileImportato:
    with transaction.atomic():
        ProblemaImportazione.objects.filter(file=file).delete()  # rielaborazione: problemi ricalcolati
        ProblemaImportazione.objects.bulk_create(c.problemi)
        file.stato = stato
        file.elaborato_il = timezone.now()
        file.righe_lette = righe_lette
        for nome in (
            "ordini_creati",
            "ordini_aggiornati",
            "ordini_scartati",
            "righe_create",
            "righe_aggiornate",
            "righe_annullate",
        ):
            setattr(file, nome, getattr(c, nome))
        file.save()
        if stato in (StatoFile.CON_ERRORI, StatoFile.NON_VALIDO):
            _notifica_errori(file)
    return file


def _notifica_errori(file: FileImportato) -> None:
    destinatari = config.leggi("importazione.notifica_errori")
    if not destinatari:
        return
    contesto = {
        "file": file,
        "problemi": file.problemi.filter(livello=Livello.ERRORE)[:50],
        "url": settings.SITE_URL + f"/importazione/file/{file.pk}/",
    }
    email.accoda(
        oggetto=f"[Metodo v2] Errori nell'importazione di {file.nome}",
        corpo_testo=render_to_string("importazione/email_errori.txt", contesto),
        mittente=config.leggi("email.mittente") or settings.DEFAULT_FROM_EMAIL,
        destinatari=destinatari,
        riferimento=f"import:{file.pk}",
    )


def _problema(
    file: FileImportato,
    c: _Conteggi,
    livello: str,
    chiave: ChiaveOrdine,
    ordine: Ordine | None,
    messaggio: str,
    riga_file: int | None = None,
) -> None:
    c.problemi.append(
        ProblemaImportazione(
            file=file,
            livello=livello,
            chiave_ordine=str(chiave),
            ordine=ordine,
            messaggio=messaggio,
            riga_file=riga_file,
        )
    )


def _applica_ordine(file: FileImportato, chiave: ChiaveOrdine, righe: list[RigaFile], c: _Conteggi) -> None:
    scadenza = min(r.data_scadenza for r in righe)
    clienti = Counter(r.cliente for r in righe if r.cliente)
    cliente = clienti.most_common(1)[0][0] if clienti else ""

    ordine = (
        Ordine.objects.select_for_update()
        .filter(serie=chiave.serie, numero=chiave.numero, appendice=chiave.appendice, anno=chiave.anno)
        .first()
    )
    if ordine is None:
        ordine = Ordine.objects.create(
            serie=chiave.serie,
            numero=chiave.numero,
            appendice=chiave.appendice,
            anno=chiave.anno,
            data_scadenza=scadenza,
            cliente=cliente,
        )
        c.ordini_creati += 1
        registra_scadenza(ordine, None, scadenza, OrigineModificaScadenza.IMPORTAZIONE, motivo=f"File {file.nome}")
    else:
        if ordine.stato == StatoOrdine.CONSEGNATO:
            c.ordini_scartati += 1
            _problema(
                file,
                c,
                Livello.ERRORE,
                chiave,
                ordine,
                f"Ordine {chiave} già consegnato: la reimportazione è stata ignorata.",
            )
            return
        c.ordini_aggiornati += 1
        if ordine.stato == StatoOrdine.ANNULLATO:
            _problema(
                file,
                c,
                Livello.AVVISO,
                chiave,
                ordine,
                f"Ordine {chiave} annullato: righe aggiornate, stato lasciato 'Annullato'.",
            )
        elif ordine.stato in (
            StatoOrdine.IN_LAVORAZIONE,
            StatoOrdine.IN_SPEDIZIONE,
            StatoOrdine.EVASIONE_PARZIALE,
        ) and config.leggi("importazione.ripristina_stato_su_reimport"):
            _problema(
                file,
                c,
                Livello.AVVISO,
                chiave,
                ordine,
                f"Ordine {chiave} reimportato: stato riportato da '{ordine.get_stato_display()}' a 'Inserito'.",
            )
            ordine.righe.attive().filter(bolla_emessa=False).update(in_evasione=False)
            ordine.stato = StatoOrdine.INSERITO
        if ordine.data_scadenza != scadenza:
            registra_scadenza(
                ordine, ordine.data_scadenza, scadenza, OrigineModificaScadenza.IMPORTAZIONE, motivo=f"File {file.nome}"
            )
            ordine.data_scadenza = scadenza
        if cliente:
            ordine.cliente = cliente
        ordine.save()

    _sincronizza_righe(file, ordine, chiave, righe, c)


def _sincronizza_righe(
    file: FileImportato, ordine: Ordine, chiave: ChiaveOrdine, righe: list[RigaFile], c: _Conteggi
) -> None:
    esistenti = {
        r.n_riga: r for r in ordine.righe.select_for_update().filter(stato=StatoRiga.ATTIVA, riga_origine__isnull=True)
    }
    for nuova in righe:
        attuale = esistenti.pop(nuova.n_riga, None)
        if attuale is None:
            _crea_riga(file, ordine, nuova)
            c.righe_create += 1
        elif (attuale.articolo, attuale.variante) != (nuova.articolo, nuova.variante):
            if attuale.bolla_emessa:
                _problema(
                    file,
                    c,
                    Livello.AVVISO,
                    chiave,
                    ordine,
                    f"Riga {nuova.n_riga}: era {attuale.articolo} (già in bolla), ora {nuova.articolo}.",
                    nuova.riga_file,
                )
            _chiudi_riga(attuale, StatoRiga.SOSTITUITA)
            _crea_riga(file, ordine, nuova)
            c.righe_create += 1
        elif _aggiorna_riga(file, ordine, chiave, attuale, nuova, c):
            c.righe_aggiornate += 1

    for mancante in esistenti.values():
        if mancante.bolla_emessa:
            _problema(
                file,
                c,
                Livello.AVVISO,
                chiave,
                ordine,
                f"Riga {mancante.n_riga} ({mancante.articolo}) già in bolla ma assente dal nuovo file: "
                "lasciata invariata.",
            )
            continue
        _chiudi_riga(mancante, StatoRiga.ANNULLATA)
        c.righe_annullate += 1


def _crea_riga(file: FileImportato, ordine: Ordine, r: RigaFile) -> RigaOrdine:
    return RigaOrdine.objects.create(
        ordine=ordine,
        n_riga=r.n_riga,
        articolo=r.articolo,
        variante=r.variante,
        qta=r.qta,
        prezzo_unitario=r.prezzo,
        sconto=r.sconto,
        cliente=r.cliente,
        magazzino=r.magazzino,
        data_scadenza=r.data_scadenza,
        origine=OrigineRiga.IMPORTAZIONE,
        ultimo_file_importato=file,
    )


def _chiudi_riga(riga: RigaOrdine, stato: str) -> None:
    """Annulla/sostituisce la riga e le sue divisioni non ancora in bolla."""
    for figlia in riga.divisioni.filter(stato=StatoRiga.ATTIVA, bolla_emessa=False):
        _chiudi_riga(figlia, stato)
    riga.stato = stato
    riga.in_evasione = False
    riga.save()


def _aggiorna_riga(
    file: FileImportato, ordine: Ordine, chiave: ChiaveOrdine, riga: RigaOrdine, nuova: RigaFile, c: _Conteggi
) -> bool:
    qta_divise = riga.divisioni.filter(stato=StatoRiga.ATTIVA).aggregate(t=Sum("qta"))["t"] or Decimal(0)
    qta_propria = nuova.qta - qta_divise
    modifiche: dict[str, Any] = {}
    if qta_propria != riga.qta:
        modifiche["qta"] = qta_propria
    for campo, valore in (
        ("prezzo_unitario", nuova.prezzo),
        ("sconto", nuova.sconto),
        ("data_scadenza", nuova.data_scadenza),
    ):
        if getattr(riga, campo) != valore:
            modifiche[campo] = valore
    if nuova.cliente and riga.cliente != nuova.cliente:
        modifiche["cliente"] = nuova.cliente
    if nuova.magazzino and riga.magazzino != nuova.magazzino:
        modifiche["magazzino"] = nuova.magazzino
    if not modifiche:
        return False
    if riga.bolla_emessa:
        _problema(
            file,
            c,
            Livello.AVVISO,
            chiave,
            ordine,
            f"Riga {riga.n_riga}: già in bolla, variazioni ignorate ({', '.join(modifiche)}).",
            nuova.riga_file,
        )
        return False
    if qta_propria < 0:
        _problema(
            file,
            c,
            Livello.AVVISO,
            chiave,
            ordine,
            f"Riga {riga.n_riga}: nuova quantità {nuova.qta} inferiore a quella già divisa ({qta_divise}). "
            "Quantità non aggiornata: verificare le divisioni.",
            nuova.riga_file,
        )
        modifiche.pop("qta")
    if "data_scadenza" in modifiche:
        registra_scadenza(
            ordine,
            riga.data_scadenza,
            nuova.data_scadenza,
            OrigineModificaScadenza.IMPORTAZIONE,
            riga=riga,
            motivo=f"File {file.nome}",
        )
    for campo, valore in modifiche.items():
        setattr(riga, campo, valore)
    riga.ultimo_file_importato = file
    riga.save()
    return True


def importa_caricamento(nome: str, contenuto: bytes, utente: Any) -> FileImportato:
    file = registra_file(nome, contenuto, OrigineFile.CARICAMENTO, utente=utente)
    return importa(file)
