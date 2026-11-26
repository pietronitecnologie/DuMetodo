"""Stato della casella degli ordini e momento da cui si leggono i messaggi (contatore a data).

    manage.py casella_ordini                    stato delle caselle
    manage.py casella_ordini --da-ora           si leggono solo i messaggi che arrivano da adesso
    manage.py casella_ordini --dal 2026-11-20   si leggono i messaggi arrivati da quel giorno (attenzione)

Metodo v2 legge i messaggi per data di arrivo, non per la marca «letto/non letto»: la stessa casella può
essere letta anche dal Metodo precedente. La prima volta parte da adesso: i messaggi vecchi, se reimportati,
riaprirebbero ordini chiusi. --dal serve solo per recuperare ordini arrivati e mai importati; quelli già
elaborati da Metodo v2 vengono comunque saltati.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from metodo.importazione import posta
from metodo.importazione.models import StatoCasella


class Command(BaseCommand):
    help = "Stato della casella degli ordini; --da-ora o --dal AAAA-MM-GG per scegliere da quando leggere."

    def add_arguments(self, parser: Any) -> None:
        gruppo = parser.add_mutually_exclusive_group()
        gruppo.add_argument("--da-ora", action="store_true", help="si leggono solo i messaggi che arrivano da adesso")
        gruppo.add_argument("--dal", metavar="AAAA-MM-GG", help="si leggono i messaggi arrivati da quel giorno")

    def handle(self, *args: Any, **opzioni: Any) -> None:
        if opzioni["da_ora"] or opzioni["dal"]:
            dal = None
            if opzioni["dal"]:
                try:
                    giorno = dt.date.fromisoformat(opzioni["dal"])
                except ValueError as exc:
                    raise CommandError("Data non valida: usa AAAA-MM-GG (es. 2026-11-20).") from exc
                dal = timezone.make_aware(dt.datetime.combine(giorno, dt.time()))
            nome = posta.imposta_inizio(dal)
            if dal:
                self.stdout.write(
                    self.style.WARNING(f"{nome}: alla prossima lettura, i messaggi arrivati dal {giorno:%d/%m/%Y}.")
                )
            else:
                self.stdout.write(self.style.SUCCESS(f"{nome}: si leggono solo i messaggi che arrivano da adesso."))
            return
        caselle = StatoCasella.objects.order_by("casella")
        if not caselle:
            self.stdout.write("Nessuna casella ancora letta: la prima lettura parte dai messaggi in arrivo da allora.")
        for s in caselle:
            fino = f"{timezone.localtime(s.letto_fino_al):%d/%m/%Y %H:%M}" if s.letto_fino_al else "—"
            quando = f"{timezone.localtime(s.ultima_lettura_il):%d/%m/%Y %H:%M}" if s.ultima_lettura_il else "mai"
            self.stdout.write(f"{s.casella}: letti i messaggi arrivati fino al {fino} · ultima lettura {quando}")
            if s.ultimo_errore:
                self.stdout.write(self.style.ERROR(f"  errore: {s.ultimo_errore}"))
