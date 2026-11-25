"""Importa ordini, righe, note, storico scadenze, consegne/DDT, reparti e utenti da Metodo.

    python manage.py importa_da_metodo --db postgres://utente:password@host/metodo            # prova a secco
    python manage.py importa_da_metodo --db postgres://utente:password@host/metodo --applica

Metodo viene letto in sola lettura. Senza --applica tutto avviene in una transazione annullata
alla fine: si ottiene il report completo delle anomalie senza modificare Metodo v2.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from metodo.migrazione.importatore import Importatore
from metodo.migrazione.sorgente import SchemaNonCompatibile, connetti
from metodo.ordini.models import Ordine


class _ProvaASecco(Exception):
    pass


class Command(BaseCommand):
    help = "Importa i dati di produzione da Metodo (sola lettura su Metodo)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--db", required=True, help="URL PostgreSQL del database di Metodo")
        parser.add_argument("--applica", action="store_true", help="Salva davvero (default: prova a secco)")
        parser.add_argument("--max-avvisi", type=int, default=20, help="Avvisi mostrati per categoria")

    def handle(self, *args: Any, **opts: Any) -> None:
        if Ordine.objects.exists():
            raise CommandError("Metodo v2 contiene già degli ordini: la migrazione va fatta su un database vuoto.")
        try:
            with connetti(opts["db"]) as metodo, transaction.atomic():
                report = Importatore(metodo).esegui()
                self._stampa(report, opts["max_avvisi"])
                if not opts["applica"]:
                    raise _ProvaASecco
        except _ProvaASecco:
            self.stdout.write(self.style.NOTICE("Prova a secco: nessun dato salvato. Rilancia con --applica."))
            return
        except SchemaNonCompatibile as exc:
            raise CommandError(f"Schema di Metodo non compatibile: {exc}") from exc
        self.stdout.write(self.style.SUCCESS("Migrazione completata."))

    def _stampa(self, report: Any, massimo: int) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("Importati:"))
        for k, v in report.conteggi.items():
            self.stdout.write(f"  {k}: {v}")
        for categoria, voci in report.avvisi.items():
            self.stdout.write(self.style.WARNING(f"Avvisi '{categoria}' ({len(voci)}):"))
            for v in voci[:massimo]:
                self.stdout.write(f"  - {v}")
            if len(voci) > massimo:
                self.stdout.write(f"  ... altri {len(voci) - massimo}")
