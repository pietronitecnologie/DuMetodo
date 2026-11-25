"""Controlli dell'applicazione: database, migrazioni, cache, worker, lavori periodici, casella, email, spazio.

Uscita: 0 tutto ok, 1 qualcosa da guardare, 2 errori. Con --json il risultato è leggibile da MetodoConsole.
"""

import json

from django.core.management.base import BaseCommand

from metodo.core import diagnostica as d

SIMBOLI = {d.OK: "OK  ", d.ATTENZIONE: "!!  ", d.ERRORE: "ERR "}


class Command(BaseCommand):
    help = "Diagnostica di Metodo v2 (codice di uscita: 0 ok, 1 attenzione, 2 errore)."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="risultato in JSON")

    def handle(self, *args, **opzioni):
        r = d.riepilogo()
        if opzioni["json"]:
            self.stdout.write(json.dumps(r, ensure_ascii=False))
        else:
            for c in r["controlli"]:
                self.stdout.write(f"{SIMBOLI[c['esito']]}{c['nome']:<26} {c['dettaglio']}")
            self.stdout.write(f"Esito: {r['esito']} (versione {r['versione']})")
        raise SystemExit(d.GRAVITA[r["esito"]])
