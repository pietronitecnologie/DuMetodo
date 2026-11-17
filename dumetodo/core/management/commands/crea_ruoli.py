from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from dumetodo.core.ruoli import Ruolo


class Command(BaseCommand):
    help = "Crea i gruppi corrispondenti ai ruoli applicativi (idempotente)."

    def handle(self, *args: object, **options: object) -> None:
        for ruolo in Ruolo:
            _, creato = Group.objects.get_or_create(name=ruolo.value)
            self.stdout.write(f"{'creato' if creato else 'esistente'}: {ruolo.value}")
