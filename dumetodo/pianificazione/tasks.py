import logging

from celery import shared_task

from dumetodo.configurazione import servizi as config
from dumetodo.ordini.servizi import OperazioneNonConsentita

from . import servizi

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def invia_report_pianificazione() -> str:
    """Celery beat: giorni lavorativi alle 6:00 (UN solo processo beat: niente invii doppi come in Metodo)."""
    if not config.leggi("pianificazione.report_automatico"):
        return "disattivato"
    try:
        servizi.invia_report()
    except OperazioneNonConsentita as exc:
        logger.warning("Report di pianificazione non inviato: %s", exc)
        return str(exc)
    return "accodato"
