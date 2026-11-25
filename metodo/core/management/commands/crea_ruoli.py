from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from metodo.core.permessi import GRUPPO_CLIENTE, sincronizza_permessi


class Command(BaseCommand):
    help = "Crea il gruppo Cliente e sincronizza i permessi per funzione (idempotente)."

    def handle(self, *args: object, **options: object) -> None:
        Group.objects.get_or_create(name=GRUPPO_CLIENTE)
        sincronizza_permessi()
        self.stdout.write("Permessi sincronizzati. Assegna i profili con: manage.py assegna_profilo <utente> <profilo>")
