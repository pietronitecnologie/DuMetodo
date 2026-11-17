"""Logica di business sugli ordini. Le viste chiamano SOLO queste funzioni.

Ogni operazione:
- gira in una transazione;
- blocca la riga dell'ordine (SELECT ... FOR UPDATE) per evitare modifiche concorrenti;
- verifica i permessi lato server (non basta nascondere i bottoni nel template);
- solleva ``OperazioneNonConsentita`` con un messaggio leggibile in caso di errore.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction
from django.db.models import Sum

from dumetodo.configurazione import servizi as config
from dumetodo.core.permessi import F, ha_permesso

from . import stati
from .models import (
    Ordine,
    OrigineModificaScadenza,
    OrigineRiga,
    RigaOrdine,
    StatoOrdine,
    StatoRiga,
    StoricoScadenza,
)


class OperazioneNonConsentita(Exception):
    pass


def _blocca_ordine(ordine_id: int) -> Ordine:
    return Ordine.objects.select_for_update().get(pk=ordine_id)


# --------------------------------------------------------------------------- stato ordine
@transaction.atomic
def cambia_stato(ordine_id: int, nuovo_stato: str, utente: Any) -> Ordine:
    ordine = _blocca_ordine(ordine_id)
    if nuovo_stato not in StatoOrdine.values:
        raise OperazioneNonConsentita(f"Stato sconosciuto: {nuovo_stato!r}.")
    if ordine.stato == nuovo_stato:
        return ordine
    t = stati.transizione(ordine.stato, nuovo_stato)
    if t is None:
        raise OperazioneNonConsentita(
            f"Non è possibile passare da '{ordine.get_stato_display()}' a '{StatoOrdine(nuovo_stato).label}'."
        )
    if not ha_permesso(utente, t.permesso):
        raise OperazioneNonConsentita("Non hai i permessi per questa operazione.")

    if nuovo_stato == StatoOrdine.IN_SPEDIZIONE:
        righe = ordine.righe.attive()
        if righe.filter(bloccata=True).exists():
            # Regola di Metodo: con righe bloccate la spedizione diventa parziale e
            # tutte le righe non bloccate e non ancora in bolla vanno in evasione.
            righe.filter(bloccata=False, bolla_emessa=False).update(in_evasione=True)
            nuovo_stato = StatoOrdine.EVASIONE_PARZIALE
    if nuovo_stato == StatoOrdine.INSERITO and ordine.stato in (
        StatoOrdine.IN_SPEDIZIONE,
        StatoOrdine.EVASIONE_PARZIALE,
    ):
        # "Rimuovi da consegna": le righe non ancora in bolla escono dall'evasione.
        ordine.righe.attive().filter(bolla_emessa=False).update(in_evasione=False)
    if nuovo_stato == StatoOrdine.CONSEGNATO and ordine.data_consegna is None:
        ordine.data_consegna = dt.date.today()

    ordine.stato = nuovo_stato
    ordine.save()
    return ordine


# --------------------------------------------------------------------------- azioni sulle righe
AZIONI_RIGA = ("evadi", "blocca", "scorpora", "reincorpora", "annulla")


def _riga_modificabile(ordine: Ordine, riga: RigaOrdine, utente: Any) -> None:
    if not riga.is_attiva:
        raise OperazioneNonConsentita("La riga non è attiva.")
    if riga.bolla_emessa:
        raise OperazioneNonConsentita("La riga è già stata inserita in una bolla.")
    if ordine.stato in (StatoOrdine.IN_SPEDIZIONE, StatoOrdine.CONSEGNATO, StatoOrdine.ANNULLATO) and not ha_permesso(
        utente, F.ORDINI_ANNULLA
    ):
        raise OperazioneNonConsentita("L'ordine è in spedizione o chiuso: righe non modificabili.")


@transaction.atomic
def azione_riga(riga_id: int, azione: str, utente: Any) -> RigaOrdine:
    """Azioni sulla singola riga. NON cambiano lo stato dell'ordine, salvo 'evadi'
    che porta l'ordine in evasione parziale (comportamento di Metodo)."""
    if azione not in AZIONI_RIGA:
        raise OperazioneNonConsentita(f"Azione sconosciuta: {azione!r}.")
    if not ha_permesso(utente, F.ORDINI_RIGHE):
        raise OperazioneNonConsentita("Non hai i permessi per questa operazione.")
    ordine_id = RigaOrdine.objects.values_list("ordine_id", flat=True).get(pk=riga_id)
    ordine = _blocca_ordine(ordine_id)
    riga = RigaOrdine.objects.select_for_update().get(pk=riga_id)

    if azione in ("scorpora", "reincorpora"):
        # Lo scorporo riguarda la fatturazione: possibile anche su righe consegnate, ma solo per gli admin.
        if not riga.is_attiva:
            raise OperazioneNonConsentita("La riga non è attiva.")
        if (riga.bolla_emessa or ordine.is_chiuso) and not ha_permesso(
            utente, F.ORDINI_ANNULLA, F.UTILITA_SCORPORAZIONE
        ):
            raise OperazioneNonConsentita("Non hai i permessi per scorporare righe già consegnate.")
        riga.scorporata = azione == "scorpora"
        riga.save()
        return riga

    _riga_modificabile(ordine, riga, utente)
    if azione == "evadi":
        riga.bloccata = False
        riga.in_evasione = True
        riga.save()
        if ordine.stato in (StatoOrdine.INSERITO, StatoOrdine.IN_LAVORAZIONE):
            ordine.stato = StatoOrdine.EVASIONE_PARZIALE
            ordine.save()
    elif azione == "blocca":
        riga.in_evasione = False
        riga.bloccata = True
        riga.save()
    elif azione == "annulla":
        if not ha_permesso(utente, F.ORDINI_ANNULLA):
            raise OperazioneNonConsentita("Non hai i permessi per annullare una riga.")
        riga.stato = StatoRiga.ANNULLATA
        riga.in_evasione = False
        riga.save()
    return riga


# --------------------------------------------------------------------------- scadenze
def registra_scadenza(
    ordine: Ordine,
    precedente: dt.date | None,
    nuova: dt.date | None,
    origine: str,
    *,
    riga: RigaOrdine | None = None,
    utente: Any = None,
    motivo: str = "",
) -> None:
    if precedente == nuova:
        return
    StoricoScadenza.objects.create(
        ordine=ordine,
        riga=riga,
        precedente=precedente,
        nuova=nuova,
        origine=origine,
        utente=utente if getattr(utente, "is_authenticated", False) else None,
        motivo=motivo,
    )


@transaction.atomic
def cambia_scadenza_ordine(ordine_id: int, nuova: dt.date, motivo: str, utente: Any) -> Ordine:
    if not ha_permesso(utente, F.ORDINI_DIVIDI):
        raise OperazioneNonConsentita("Non hai i permessi per modificare la scadenza.")
    if not motivo.strip():
        raise OperazioneNonConsentita("Indica il motivo della modifica.")
    ordine = _blocca_ordine(ordine_id)
    if ordine.is_chiuso:
        raise OperazioneNonConsentita("L'ordine è chiuso.")
    registra_scadenza(ordine, ordine.data_scadenza, nuova, OrigineModificaScadenza.UTENTE, utente=utente, motivo=motivo)
    ordine.data_scadenza = nuova
    ordine.save()
    for riga in ordine.righe.attive().filter(bolla_emessa=False).exclude(data_scadenza=nuova):
        riga.data_scadenza = nuova
        riga.save(update_fields=["data_scadenza", "aggiornato_il"])
    return ordine


# --------------------------------------------------------------------------- divisione riga
def _numero_divisione(riga: RigaOrdine) -> str:
    base = riga.n_riga.split("/")[0]
    esistenti = set(
        RigaOrdine.objects.filter(ordine=riga.ordine, n_riga__startswith=f"{base}/").values_list("n_riga", flat=True)
    )
    k = 2
    while f"{base}/{k}" in esistenti:
        k += 1
    return f"{base}/{k}"


@transaction.atomic
def dividi_riga(riga_id: int, qta: Decimal | str, utente: Any, nuova_scadenza: dt.date | None = None) -> RigaOrdine:
    """Separa ``qta`` pezzi dalla riga in una nuova riga dello stesso ordine, eventualmente
    con un'altra scadenza. Ritorna la nuova riga.

    In Metodo la divisione con data diversa creava un nuovo ordine ``numero_AAAA-MM-GG``;
    qui la riga resta nell'ordine originale con la propria scadenza (tracciata nello storico),
    così la chiave d'ordine del cliente resta intatta e la consegna/fatturazione è coerente.
    """
    if not ha_permesso(utente, F.ORDINI_DIVIDI):
        raise OperazioneNonConsentita("Non hai i permessi per dividere una riga.")
    try:
        qta = Decimal(str(qta).replace(",", "."))
    except InvalidOperation as exc:
        raise OperazioneNonConsentita("Quantità non valida.") from exc
    ordine_id = RigaOrdine.objects.values_list("ordine_id", flat=True).get(pk=riga_id)
    ordine = _blocca_ordine(ordine_id)
    riga = RigaOrdine.objects.select_for_update().get(pk=riga_id)
    _riga_modificabile(ordine, riga, utente)
    if riga.in_evasione:
        raise OperazioneNonConsentita("La riga è in evasione: rimuovila dalla consegna prima di dividerla.")
    if qta <= 0 or qta >= riga.qta:
        raise OperazioneNonConsentita(f"La quantità da separare deve essere maggiore di 0 e minore di {riga.qta}.")

    riga.qta -= qta
    riga.save()
    nuova = RigaOrdine.objects.create(
        ordine=ordine,
        n_riga=_numero_divisione(riga),
        articolo=riga.articolo,
        variante=riga.variante,
        qta=qta,
        prezzo_unitario=riga.prezzo_unitario,
        sconto=riga.sconto,
        cliente=riga.cliente,
        magazzino=riga.magazzino,
        data_scadenza=nuova_scadenza or riga.data_scadenza,
        origine=OrigineRiga.DIVISIONE,
        riga_origine=riga,
        bloccata=riga.bloccata,
        scorporata=riga.scorporata,
    )
    registra_scadenza(
        ordine,
        riga.data_scadenza,
        nuova.data_scadenza,
        OrigineModificaScadenza.DIVISIONE,
        riga=nuova,
        utente=utente,
        motivo=f"Divisione della riga {riga.n_riga}",
    )
    return nuova


# --------------------------------------------------------------------------- calcoli
def ore_teoriche(ordine: Ordine) -> Decimal | None:
    """Σ(qtà × prezzo) / resa oraria. None se la resa oraria non è configurata."""
    resa = config.leggi("produzione.prezzo_resa_oraria")
    if not resa:
        return None
    totale = Decimal(0)
    for qta, prezzo in ordine.righe.attive().exclude(prezzo_unitario=None).values_list("qta", "prezzo_unitario"):
        totale += qta * prezzo
    return (totale / resa).quantize(Decimal("0.01"))


def totale_pezzi(ordine: Ordine) -> Decimal:
    return ordine.righe.attive().aggregate(t=Sum("qta"))["t"] or Decimal(0)
