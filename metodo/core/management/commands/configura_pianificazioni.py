"""Crea/aggiorna i lavori periodici in Celery beat (idempotente).

Sostituisce APScheduler di Metodo, che partiva dentro OGNI processo web: con più worker
la lettura della posta e il report giravano più volte in parallelo.
"""

from django.core.management.base import BaseCommand
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

LAVORI = [
    ("Lettura casella ordini", "metodo.importazione.tasks.leggi_casella_ordini", 30),
    ("Ritenta email in coda", "metodo.comunicazioni.tasks.ritenta_email_in_coda", 5),
]
# Lavori di versioni precedenti, da togliere se ancora presenti.
OBSOLETI = ["Work order batterie"]


class Command(BaseCommand):
    help = "Configura i lavori periodici (lettura ordini, coda email)."

    def handle(self, *args: object, **options: object) -> None:
        PeriodicTask.objects.filter(name__in=OBSOLETI).delete()
        for nome, task, minuti in LAVORI:
            intervallo, _ = IntervalSchedule.objects.get_or_create(every=minuti, period=IntervalSchedule.MINUTES)
            _, creato = PeriodicTask.objects.update_or_create(
                name=nome, defaults={"task": task, "interval": intervallo}
            )
            self.stdout.write(f"{'creato' if creato else 'aggiornato'}: {nome} ogni {minuti} minuti")
        alle_sei, _ = CrontabSchedule.objects.get_or_create(
            minute="0", hour="6", day_of_week="1-5", day_of_month="*", month_of_year="*", timezone="Europe/Rome"
        )
        PeriodicTask.objects.update_or_create(
            name="Report pianificazione",
            defaults={
                "task": "metodo.pianificazione.tasks.invia_report_pianificazione",
                "crontab": alle_sei,
                "interval": None,
            },
        )
        self.stdout.write("configurato: Report pianificazione, lun-ven alle 6:00")
