"""Crea il primo amministratore (idempotente): password casuale, mostrata una sola volta."""

import secrets
from typing import Any

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from metodo.core.permessi import applica_profilo


class Command(BaseCommand):
    help = "Crea l'utente amministratore iniziale se non esiste."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--username", default="admin")

    def handle(self, *args: Any, **opts: Any) -> None:
        nome = opts["username"]
        if User.objects.filter(username=nome).exists():
            self.stdout.write(f"L'utente '{nome}' esiste già: password invariata.")
            return
        password = secrets.token_urlsafe(12)
        u = User.objects.create_user(nome, password=password, is_staff=True, is_superuser=True)
        applica_profilo(u, "Amministratore")
        from metodo.core.models import ProfiloUtente

        ProfiloUtente.objects.update_or_create(utente=u, defaults={"deve_cambiare_password": True})
        self.stdout.write(self.style.SUCCESS(f"Creato l'amministratore '{nome}' con password: {password}"))
        self.stdout.write("Annotala: al primo accesso Metodo v2 chiederà di sceglierne una nuova.")
