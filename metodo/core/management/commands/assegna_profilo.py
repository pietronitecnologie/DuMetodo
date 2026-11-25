from typing import Any

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from metodo.core.permessi import PROFILI, applica_profilo


class Command(BaseCommand):
    help = "Assegna a un utente i permessi di un profilo (es. Operatore, Amministratore, Amministrazione)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("utente")
        parser.add_argument("profilo", choices=list(PROFILI))

    def handle(self, *args: Any, **opts: Any) -> None:
        try:
            utente = User.objects.get(username=opts["utente"])
        except User.DoesNotExist as exc:
            raise CommandError(f"Utente {opts['utente']!r} inesistente.") from exc
        applica_profilo(utente, opts["profilo"])
        self.stdout.write(self.style.SUCCESS(f"Profilo {opts['profilo']} assegnato a {utente.username}."))
