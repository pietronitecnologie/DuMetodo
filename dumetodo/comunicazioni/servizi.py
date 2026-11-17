from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, IntegerField, OuterRef, QuerySet, Subquery
from django.db.models.functions import Coalesce
from django.template.loader import render_to_string
from django.utils import timezone

from dumetodo.configurazione import servizi as config
from dumetodo.core.permessi import F, ha_permesso, is_cliente, is_fornitore
from dumetodo.ordini.models import Ordine

from . import email
from .models import Lato, LetturaNota, Nota, Visibilita

LUNGHEZZA_MASSIMA = 5000


class NotaNonValida(Exception):
    pass


def _lato(utente: Any) -> str:
    if not ha_permesso(utente, F.NOTE_SCRIVERE):
        raise NotaNonValida("Non hai i permessi per scrivere note.")
    return Lato.CLIENTE if is_cliente(utente) else Lato.FORNITORE


def note_visibili(ordine: Ordine, utente: Any) -> QuerySet[Nota]:
    return ordine.note.per_utente(is_fornitore(utente)).select_related("autore")


@transaction.atomic
def aggiungi_nota(ordine: Ordine, utente: Any, testo: str, visibilita: str = Visibilita.CONDIVISA) -> Nota:
    testo = (testo or "").strip()
    if not testo:
        raise NotaNonValida("La nota è vuota.")
    if len(testo) > LUNGHEZZA_MASSIMA:
        raise NotaNonValida(f"La nota supera i {LUNGHEZZA_MASSIMA} caratteri.")
    lato = _lato(utente)
    if lato == Lato.CLIENTE or visibilita not in Visibilita.values:
        visibilita = Visibilita.CONDIVISA
    nota = Nota.objects.create(
        ordine=ordine,
        autore=utente,
        autore_nome=utente.get_full_name() or utente.get_username(),
        lato=lato,
        visibilita=visibilita,
        testo=testo,
    )
    LetturaNota.objects.create(nota=nota, utente=utente)  # chi scrive l'ha letta
    if visibilita == Visibilita.CONDIVISA and config.leggi("note.notifica_email"):
        _accoda_notifica(nota)
    return nota


def _accoda_notifica(nota: Nota) -> None:
    """Il Fornitore scrive -> avvisa il Cliente e viceversa. Rispondi-a = il lato di chi ha scritto."""
    fornitore, cliente = config.leggi("email.fornitore"), config.leggi("email.cliente")
    destinatari, rispondi_a = (cliente, fornitore) if nota.lato == Lato.FORNITORE else (fornitore, cliente)
    mittente = config.leggi("email.mittente") or settings.DEFAULT_FROM_EMAIL
    lato = config.leggi("azienda.nome_cliente" if nota.lato == Lato.CLIENTE else "azienda.nome_fornitore")
    contesto = {
        "nota": nota,
        "ordine": nota.ordine,
        "lato": lato,
        "url": settings.SITE_URL + nota.ordine.get_absolute_url(),
    }
    email.accoda(
        oggetto=f"[DuMetodo][Nota ordine {nota.ordine.codice_breve}]",
        corpo_testo=render_to_string("comunicazioni/email_nota.txt", contesto),
        corpo_html=render_to_string("comunicazioni/email_nota.html", contesto),  # autoescape attivo
        mittente=mittente,
        destinatari=destinatari,
        rispondi_a=rispondi_a,
        riferimento=f"nota:{nota.pk}",
    )


@transaction.atomic
def elimina_nota(nota: Nota, utente: Any) -> None:
    """Eliminazione logica: resta nel database per l'audit. Può farlo l'autore o un amministratore."""
    if nota.eliminata:
        return
    if nota.autore_id != utente.pk and not ha_permesso(utente, F.NOTE_ELIMINARE_TUTTE):
        raise NotaNonValida("Puoi eliminare solo le tue note.")
    nota.eliminata_il = timezone.now()
    nota.eliminata_da = utente
    nota.save(update_fields=["eliminata_il", "eliminata_da"])


def segna_lette(ordine: Ordine, utente: Any) -> int:
    da_leggere = note_visibili(ordine, utente).exclude(letture__utente=utente).values_list("pk", flat=True)
    nuove = [LetturaNota(nota_id=pk, utente=utente) for pk in da_leggere]
    LetturaNota.objects.bulk_create(nuove, ignore_conflicts=True)
    return len(nuove)


def annota_non_lette(ordini: QuerySet[Ordine], utente: Any) -> QuerySet[Ordine]:
    """Aggiunge ``note_non_lette`` a ogni ordine della lista con una subquery (niente N+1)."""
    note = Nota.objects.per_utente(is_fornitore(utente)).filter(ordine=OuterRef("pk"))
    note = note.exclude(Exists(LetturaNota.objects.filter(nota=OuterRef("pk"), utente=utente)))
    conteggio = note.order_by().values("ordine").annotate(n=Count("pk")).values("n")
    return ordini.annotate(note_non_lette=Coalesce(Subquery(conteggio, output_field=IntegerField()), 0))
