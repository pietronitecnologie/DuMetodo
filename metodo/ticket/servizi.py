"""Operazioni sui ticket e calcolo delle ore (totali, in attesa, effettive, di risposta)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from metodo.configurazione import servizi as config
from metodo.core.permessi import F, ha_permesso

from .models import EventoTicket, Priorita, Stato, Ticket, TipoEvento

CHIAVE_SOGLIA = {
    Priorita.URGENTE: "ticket.ore_massime_urgente",
    Priorita.ALTA: "ticket.ore_massime_alta",
    Priorita.NORMALE: "ticket.ore_massime_normale",
    Priorita.BASSA: "ticket.ore_massime_bassa",
}


# ------------------------------------------------------------------------------------------------------ ore
def _ore(secondi: float) -> Decimal:
    return (Decimal(max(secondi, 0)) / 3600).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def testo_ore(ore: Decimal | None) -> str:
    """12.5 → «12,5 h»; da 48 ore in su anche i giorni: «3 g 4 h»."""
    if ore is None:
        return "—"
    if ore >= 48:
        giorni, resto = divmod(int(ore), 24)
        return f"{giorni} g {resto} h"
    return f"{ore:.1f} h".replace(".", ",")


@dataclass
class Ore:
    totali: Decimal  # dall'apertura alla chiusura (o a adesso, se aperto)
    attesa: Decimal  # passate «in attesa»
    risposta: Decimal | None  # dall'apertura alla presa in carico
    soglia: int | None  # ore massime per la priorità (configurazione)
    in_corso: bool  # ticket ancora aperto: le ore stanno ancora salendo

    @property
    def effettive(self) -> Decimal:
        return max(self.totali - self.attesa, Decimal(0))

    @property
    def in_ritardo(self) -> bool:
        return self.soglia is not None and self.effettive > self.soglia

    @property
    def frazione(self) -> float:
        """Quanto della soglia è già passato (1 = scaduto), per la barra."""
        return float(self.effettive / self.soglia) if self.soglia else 0.0

    @property
    def testo_totali(self) -> str:
        return testo_ore(self.totali)

    @property
    def testo_effettive(self) -> str:
        return testo_ore(self.effettive)

    @property
    def testo_attesa(self) -> str:
        return testo_ore(self.attesa)

    @property
    def testo_risposta(self) -> str:
        return testo_ore(self.risposta)


def soglia(priorita: str) -> int | None:
    chiave = CHIAVE_SOGLIA.get(priorita)  # type: ignore[call-overload]
    return config.leggi(chiave) if chiave else None


def ore(ticket: Ticket, adesso: dt.datetime | None = None, soglie: dict[str, int | None] | None = None) -> Ore:
    fine = ticket.chiuso_il or adesso or timezone.now()
    attesa = ticket.secondi_attesa + ((fine - ticket.in_attesa_dal).total_seconds() if ticket.in_attesa_dal else 0)
    risposta = (
        _ore((ticket.preso_in_carico_il - ticket.aperto_il).total_seconds()) if ticket.preso_in_carico_il else None
    )
    return Ore(
        totali=_ore((fine - ticket.aperto_il).total_seconds()),
        attesa=_ore(attesa),
        risposta=risposta,
        soglia=soglie.get(ticket.priorita) if soglie is not None else soglia(ticket.priorita),
        in_corso=ticket.chiuso_il is None,
    )


def soglie() -> dict[str, int | None]:
    """Le soglie di tutte le priorità (una lettura per elenco, non una per ticket)."""
    return {p: soglia(p) for p in Priorita.values}


def con_ore(tickets: Any, adesso: dt.datetime | None = None) -> list[Ticket]:
    """I ticket con l'attributo ``ore`` calcolato."""
    adesso = adesso or timezone.now()
    s = soglie()
    elenco = list(tickets)
    for t in elenco:
        t.ore = ore(t, adesso, s)  # type: ignore[attr-defined]
    return elenco


def nome(utente: Any) -> str:
    return (utente.get_full_name() or utente.username) if utente else ""


def incaricabili() -> QuerySet[Any]:
    """Gli utenti a cui si può assegnare un ticket: attivi, con i permessi dei ticket, non Clienti."""
    from django.contrib.auth.models import User
    from django.db.models import Q

    from metodo.core.permessi import GRUPPO_CLIENTE

    return (
        User.objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(user_permissions__codename__in=[F.TICKET_APRIRE, F.TICKET_GESTIRE]))
        .exclude(groups__name=GRUPPO_CLIENTE)
        .distinct()
        .order_by("first_name", "last_name", "username")
    )


# ------------------------------------------------------------------------------------------------- permessi
def puo_lavorare(utente: Any, ticket: Ticket) -> bool:
    """Cambiare lo stato (e chiudere): chi gestisce i ticket, chi l'ha aperto o chi ce l'ha assegnato."""
    if ha_permesso(utente, F.TICKET_GESTIRE):
        return True
    return ha_permesso(utente, F.TICKET_APRIRE) and utente.pk in (ticket.aperto_da_id, ticket.assegnato_a_id)


def puo_prendere(utente: Any, ticket: Ticket) -> bool:
    """Prendere in carico un ticket non ancora assegnato (o assegnato a sé)."""
    return (
        not ticket.is_chiuso
        and ha_permesso(utente, F.TICKET_APRIRE, F.TICKET_GESTIRE)
        and ticket.assegnato_a_id in (None, utente.pk)
        and ticket.stato != Stato.LAVORAZIONE
    )


# --------------------------------------------------------------------------------------------- operazioni
def _evento(ticket: Ticket, tipo: str, autore: Any, testo: str = "", momento: dt.datetime | None = None) -> None:
    EventoTicket.objects.create(ticket=ticket, tipo=tipo, autore=autore, testo=testo, momento=momento or timezone.now())


@transaction.atomic
def apri(utente: Any, **campi: Any) -> Ticket:
    if not ha_permesso(utente, F.TICKET_APRIRE, F.TICKET_GESTIRE):
        raise PermissionDenied
    riga = campi.get("riga")
    if riga is not None and campi.get("ordine") is None:
        campi["ordine"] = riga.ordine
    if riga is not None and riga.ordine_id != campi["ordine"].pk:
        raise ValidationError("La riga non è di questo ordine.")
    t = Ticket.objects.create(aperto_da=utente, **campi)
    _evento(t, TipoEvento.APERTURA, utente, t.descrizione, t.aperto_il)
    if t.assegnato_a_id:
        _evento(t, TipoEvento.ASSEGNAZIONE, utente, f"Assegnato a {nome(t.assegnato_a)}", t.aperto_il)
    return t


def commenta(ticket: Ticket, utente: Any, testo: str) -> None:
    if not ha_permesso(utente, F.TICKET_APRIRE, F.TICKET_GESTIRE):
        raise PermissionDenied
    testo = (testo or "").strip()
    if not testo:
        raise ValidationError("Scrivi il commento.")
    _evento(ticket, TipoEvento.COMMENTO, utente, testo)


@transaction.atomic
def cambia_stato(ticket: Ticket, utente: Any, nuovo: str, nota: str = "") -> None:
    """Porta il ticket in ``nuovo`` aggiornando i tempi; per chiudere serve la soluzione (``nota``)."""
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if nuovo not in Stato.values or nuovo == ticket.stato:
        raise ValidationError("Stato non valido.")
    nota = (nota or "").strip()
    riapertura = ticket.is_chiuso
    if riapertura:
        if not (ha_permesso(utente, F.TICKET_GESTIRE) or utente.pk == ticket.aperto_da_id):
            raise PermissionDenied
    elif not (puo_lavorare(utente, ticket) or (nuovo == Stato.LAVORAZIONE and puo_prendere(utente, ticket))):
        raise PermissionDenied
    adesso = timezone.now()
    if ticket.in_attesa_dal:  # finisce un'attesa
        ticket.secondi_attesa += int((adesso - ticket.in_attesa_dal).total_seconds())
        ticket.in_attesa_dal = None
    if nuovo == Stato.ATTESA:
        ticket.in_attesa_dal = adesso
    if nuovo == Stato.LAVORAZIONE:
        ticket.preso_in_carico_il = ticket.preso_in_carico_il or adesso
        if ticket.assegnato_a_id is None:
            ticket.assegnato_a = utente
            _evento(ticket, TipoEvento.ASSEGNAZIONE, utente, f"Preso in carico da {nome(utente)}", adesso)
    if nuovo == Stato.CHIUSO:
        if not nota:
            raise ValidationError("Per chiudere il ticket scrivi la soluzione.")
        ticket.chiuso_il, ticket.chiuso_da, ticket.soluzione = adesso, utente, nota
    elif riapertura:
        # Riaperto: le ore riprendono a contare dall'apertura originale; il tempo da chiuso conta come attesa.
        ticket.secondi_attesa += int((adesso - ticket.chiuso_il).total_seconds()) if ticket.chiuso_il else 0
        ticket.chiuso_il = ticket.chiuso_da = None
        ticket.soluzione = ""
    precedente = ticket.get_stato_display()
    ticket.stato = nuovo
    ticket.save()
    testo = f"{precedente} → {ticket.get_stato_display()}"
    _evento(ticket, TipoEvento.STATO, utente, f"{testo}\n{nota}" if nota else testo, adesso)


def assegna(ticket: Ticket, utente: Any, a: Any) -> None:
    if not ha_permesso(utente, F.TICKET_GESTIRE):
        raise PermissionDenied
    if ticket.assegnato_a_id == (a.pk if a else None):
        return
    ticket.assegnato_a = a
    ticket.save(update_fields=["assegnato_a"])
    _evento(ticket, TipoEvento.ASSEGNAZIONE, utente, f"Assegnato a {nome(a)}" if a else "Tolta l'assegnazione")


def cambia_priorita(ticket: Ticket, utente: Any, priorita: str) -> None:
    if not ha_permesso(utente, F.TICKET_GESTIRE):
        raise PermissionDenied
    if priorita not in Priorita.values:
        raise ValidationError("Priorità non valida.")
    if priorita == ticket.priorita:
        return
    precedente = ticket.get_priorita_display()
    ticket.priorita = priorita
    ticket.save(update_fields=["priorita"])
    _evento(ticket, TipoEvento.PRIORITA, utente, f"Priorità {precedente} → {ticket.get_priorita_display()}")


# ---------------------------------------------------------------------------------------------- statistiche
def statistiche(qs: QuerySet[Ticket], giorni: int = 30, adesso: dt.datetime | None = None) -> dict[str, Any]:
    """Situazione dei ticket aperti e ore medie di quelli chiusi negli ultimi ``giorni``."""
    adesso = adesso or timezone.now()
    aperti = con_ore(qs.aperti(), adesso)
    chiusi = con_ore(qs.filter(stato=Stato.CHIUSO, chiuso_il__gte=adesso - dt.timedelta(days=giorni)), adesso)

    def media(valori: list[Decimal]) -> Decimal | None:
        return (sum(valori, Decimal(0)) / len(valori)).quantize(Decimal("0.1")) if valori else None

    per_categoria: dict[str, list[Decimal]] = {}
    for t in chiusi:
        per_categoria.setdefault(t.get_categoria_display(), []).append(t.ore.effettive)  # type: ignore[attr-defined]
    risposte = [t.ore.risposta for t in [*aperti, *chiusi] if t.ore.risposta is not None]  # type: ignore[attr-defined]
    return {
        "giorni": giorni,
        "aperti": len(aperti),
        "per_stato": {s.label: sum(1 for t in aperti if t.stato == s) for s in Stato if s != Stato.CHIUSO},
        "in_ritardo": sum(1 for t in aperti if t.ore.in_ritardo),  # type: ignore[attr-defined]
        "chiusi": len(chiusi),
        "media_effettive": testo_ore(media([t.ore.effettive for t in chiusi])),  # type: ignore[attr-defined]
        "media_totali": testo_ore(media([t.ore.totali for t in chiusi])),  # type: ignore[attr-defined]
        "media_risposta": testo_ore(media(risposte)),
        "per_categoria": sorted(
            ((nome, len(v), testo_ore(media(v))) for nome, v in per_categoria.items()), key=lambda r: -r[1]
        ),
    }
