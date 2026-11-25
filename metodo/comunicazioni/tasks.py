from celery import shared_task

from . import email


@shared_task(ignore_result=True)
def invia_email(email_id: int) -> None:
    email.invia(email_id)


@shared_task(ignore_result=True)
def ritenta_email_in_coda() -> int:
    """Giro periodico (Celery beat, ogni 5 minuti): invia le email rimaste in coda o in errore."""
    ids = email.da_ritentare()
    for email_id in ids:
        email.invia(email_id)
    return len(ids)
