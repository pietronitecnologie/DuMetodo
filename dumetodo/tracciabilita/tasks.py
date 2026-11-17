from celery import shared_task

from . import servizi


@shared_task(ignore_result=True)
def genera_work_order_batterie() -> int:
    """Giro periodico (Celery beat, una volta al giorno): batterie da ricaricare.

    In Metodo il job era scritto ma disattivato e non aveva interfaccia; ora apre al massimo un
    work order per batteria, visibile e chiudibile dalla pagina Work order.
    """
    return servizi.genera_work_order_batterie()
