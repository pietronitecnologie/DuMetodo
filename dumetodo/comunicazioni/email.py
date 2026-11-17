"""Accodamento e invio delle email (pattern outbox)."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence

from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.utils import timezone

from .models import EmailInUscita, StatoEmail

logger = logging.getLogger(__name__)

MAX_TENTATIVI = 8


def accoda(
    *,
    oggetto: str,
    corpo_testo: str,
    mittente: str,
    destinatari: Sequence[str],
    corpo_html: str = "",
    cc: Sequence[str] = (),
    rispondi_a: Sequence[str] = (),
    riferimento: str = "",
) -> EmailInUscita | None:
    """Salva l'email nella coda. Va chiamata DENTRO la transazione dell'operazione che la genera:
    se l'operazione fallisce, l'email non parte; se riesce, l'email partirà di sicuro."""
    destinatari = [d for d in destinatari if d]
    if not destinatari:
        logger.warning("Email '%s' non accodata: nessun destinatario configurato", oggetto)
        return None
    email = EmailInUscita.objects.create(
        oggetto=oggetto[:255],
        corpo_testo=corpo_testo,
        corpo_html=corpo_html,
        mittente=mittente,
        destinatari=list(destinatari),
        cc=list(cc),
        rispondi_a=list(rispondi_a),
        riferimento=riferimento[:100],
    )

    def _avvia() -> None:
        from .tasks import invia_email

        try:
            invia_email.delay(email.pk)
        except Exception:  # broker non raggiungibile: ci penserà il giro periodico
            logger.exception("Impossibile avviare l'invio dell'email %s; verrà ritentato", email.pk)

    transaction.on_commit(_avvia)
    return email


def _ritardo(tentativi: int) -> dt.timedelta:
    return dt.timedelta(minutes=min(2**tentativi, 240))


def invia(email_id: int) -> bool:
    """Invia un'email della coda. Idempotente: un'email già inviata non riparte."""
    with transaction.atomic():
        email = EmailInUscita.objects.select_for_update(skip_locked=True).filter(pk=email_id).first()
        if email is None or email.stato in (StatoEmail.INVIATA, StatoEmail.FALLITA):
            return False
        messaggio = EmailMultiAlternatives(
            subject=email.oggetto,
            body=email.corpo_testo,
            from_email=email.mittente,
            to=email.destinatari,
            cc=email.cc,
            reply_to=email.rispondi_a,
        )
        if email.corpo_html:
            messaggio.attach_alternative(email.corpo_html, "text/html")
        email.tentativi += 1
        try:
            messaggio.send(fail_silently=False)
        except Exception as exc:
            email.ultimo_errore = f"{type(exc).__name__}: {exc}"[:2000]
            if email.tentativi >= MAX_TENTATIVI:
                email.stato = StatoEmail.FALLITA
                logger.error("Email %s fallita definitivamente: %s", email.pk, email.ultimo_errore)
            else:
                email.stato = StatoEmail.ERRORE
                email.prossimo_tentativo_il = timezone.now() + _ritardo(email.tentativi)
            email.save()
            return False
        email.stato = StatoEmail.INVIATA
        email.inviata_il = timezone.now()
        email.ultimo_errore = ""
        email.save()
        return True


def da_ritentare() -> list[int]:
    return list(
        EmailInUscita.objects.filter(
            stato__in=[StatoEmail.IN_CODA, StatoEmail.ERRORE], prossimo_tentativo_il__lte=timezone.now()
        ).values_list("pk", flat=True)[:100]
    )
