"""Chat 1 a 1 tra gli utenti di DuMetodo. I messaggi sono salvati sul server (MessaggioChat).

- niente chat di gruppo: ogni conversazione è tra due persone;
- si può scrivere anche a chi non è collegato: legge il messaggio quando apre il client;
- "chiudere" una conversazione la toglie dal proprio pannello (i messaggi restano); torna con il
  primo messaggio nuovo o riaprendola dall'elenco;
- chi è collegato lo dice la cache (segnale del pannello ogni pochi secondi), non il database.
"""

from __future__ import annotations

import time
from typing import Any

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from dumetodo.core.permessi import F, ha_permesso

from .models import ConversazioneChiusa, MessaggioChat

ONLINE_SECONDI = 30
STORICO = 200  # messaggi mostrati per conversazione
MASSIMO_TESTO = 2000
MESSAGGI_AL_MINUTO = 40


class ChatNonConsentita(Exception):
    pass


def _nome(u: User) -> str:
    return u.get_full_name() or u.username


def puo_chattare(u: Any) -> bool:
    return ha_permesso(u, F.CHAT)


def _utenti_chat() -> list[User]:
    return [
        u
        for u in User.objects.filter(is_active=True).order_by("first_name", "last_name", "username")
        if puo_chattare(u)
    ]


# --------------------------------------------------------------------------------------------- presenza
def segnala_presenza(u: User) -> None:
    cache.set(f"chat:presenza:{u.pk}", time.time(), timeout=ONLINE_SECONDI * 2)


def e_collegato(utente_id: int) -> bool:
    return time.time() - (cache.get(f"chat:presenza:{utente_id}") or 0) < ONLINE_SECONDI


# --------------------------------------------------------------------------------------------- messaggi
def invia(mittente: User, destinatario_id: int, testo: str) -> MessaggioChat:
    if not puo_chattare(mittente):
        raise ChatNonConsentita("Non hai il permesso di usare la chat.")
    testo = (testo or "").strip()
    if not testo:
        raise ChatNonConsentita("Il messaggio è vuoto.")
    if len(testo) > MASSIMO_TESTO:
        raise ChatNonConsentita(f"Messaggio troppo lungo (massimo {MASSIMO_TESTO} caratteri).")
    if destinatario_id == mittente.pk:
        raise ChatNonConsentita("Non puoi scrivere a te stesso.")
    destinatario = User.objects.filter(pk=destinatario_id, is_active=True).first()
    if destinatario is None or not puo_chattare(destinatario):
        raise ChatNonConsentita("Questo utente non può ricevere messaggi in chat.")
    contatore = f"chat:ritmo:{mittente.pk}:{int(time.time() // 60)}"
    cache.add(contatore, 0, timeout=120)
    if cache.incr(contatore) > MESSAGGI_AL_MINUTO:
        raise ChatNonConsentita("Troppi messaggi in poco tempo: attendi qualche secondo.")
    with transaction.atomic():
        return MessaggioChat.objects.create(mittente=mittente, destinatario=destinatario, testo=testo)


def segna_letti(u: User, altro_id: int) -> int:
    return MessaggioChat.objects.filter(destinatario=u, mittente_id=altro_id, letto_il__isnull=True).update(
        letto_il=timezone.now()
    )


@transaction.atomic
def chiudi(u: User, altro_id: int) -> None:
    ultimo = (
        MessaggioChat.objects.filter(
            Q(mittente=u, destinatario_id=altro_id) | Q(mittente_id=altro_id, destinatario=u)
        ).aggregate(m=Max("id"))["m"]
        or 0
    )
    segna_letti(u, altro_id)
    ConversazioneChiusa.objects.update_or_create(utente=u, altro_id=altro_id, defaults={"ultimo_messaggio": ultimo})


def riapri(u: User, altro_id: int) -> None:
    ConversazioneChiusa.objects.filter(utente=u, altro_id=altro_id).delete()


def _messaggi(u: User, altro_id: int) -> list[dict[str, Any]]:
    qs = MessaggioChat.objects.filter(
        Q(mittente=u, destinatario_id=altro_id) | Q(mittente_id=altro_id, destinatario=u)
    ).order_by("-id")[:STORICO]
    oggi = timezone.localdate()
    risultato = []
    for m in reversed(list(qs)):
        quando = timezone.localtime(m.inviato_il)
        risultato.append(
            {
                "id": m.pk,
                "mio": m.mittente_id == u.pk,
                "testo": m.testo,
                "ora": quando.strftime("%H:%M") if quando.date() == oggi else quando.strftime("%d/%m %H:%M"),
                "letto": m.letto_il is not None,
            }
        )
    return risultato


def stato(u: User) -> dict[str, Any]:
    """Per il pannello: persone (con chi è collegato), conversazioni aperte con messaggi e non letti."""
    segnala_presenza(u)
    persone = [{"id": x.pk, "nome": _nome(x), "collegato": e_collegato(x.pk)} for x in _utenti_chat() if x.pk != u.pk]
    nomi = {p["id"]: p for p in persone}
    # Con chi ho scambiato messaggi, con l'ultimo id: una conversazione è aperta se non l'ho chiusa dopo.
    ultimi: dict[int, int] = {}
    for riga in (
        MessaggioChat.objects.filter(Q(mittente=u) | Q(destinatario=u))
        .values("mittente_id", "destinatario_id")
        .annotate(ultimo=Max("id"))
    ):
        altro = riga["destinatario_id"] if riga["mittente_id"] == u.pk else riga["mittente_id"]
        ultimi[altro] = max(ultimi.get(altro, 0), riga["ultimo"])
    chiuse = dict(ConversazioneChiusa.objects.filter(utente=u).values_list("altro_id", "ultimo_messaggio"))
    non_letti = dict(
        MessaggioChat.objects.filter(destinatario=u, letto_il__isnull=True)
        .values("mittente_id")
        .annotate(n=Count("id"))
        .values_list("mittente_id", "n")
    )
    conversazioni = []
    for altro, ultimo in sorted(ultimi.items(), key=lambda x: -x[1]):
        if altro in chiuse and ultimo <= chiuse[altro]:
            continue
        persona = nomi.get(altro) or {"nome": "utente non più attivo", "collegato": False}
        conversazioni.append(
            {
                "con": altro,
                "nome": persona["nome"],
                "collegato": persona["collegato"],
                "messaggi": _messaggi(u, altro),
                "non_letti": non_letti.get(altro, 0),
            }
        )
    return {
        "persone": persone,
        "conversazioni": conversazioni,
        "non_letti": sum(non_letti.values()),
        "ultimo_ricevuto": MessaggioChat.objects.filter(destinatario=u).aggregate(m=Max("id"))["m"] or 0,
    }
