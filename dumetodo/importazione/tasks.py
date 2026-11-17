import logging

from celery import shared_task

from . import posta

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def leggi_casella_ordini() -> str:
    """Pianificato con Celery beat (default ogni 30 minuti, come in Metodo)."""
    esito = posta.leggi_casella()
    msg = f"messaggi={esito.messaggi} file={esito.file}" if esito.eseguita else f"saltata: {esito.motivo}"
    logger.info("Lettura casella ordini: %s", msg)
    return msg
