"""Invia un avviso di MetodoConsole ai destinatari di Configurazione → Indirizzi email → avvisi del server.

Il testo si legge dallo standard input. Uscita 3 se non ci sono destinatari configurati.
"""

import sys

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from metodo.configurazione import servizi as config


class Command(BaseCommand):
    help = "Invia un avviso del server via email (testo dallo standard input)."

    def add_arguments(self, parser):
        parser.add_argument("--oggetto", required=True)

    def handle(self, *args, **opzioni):
        destinatari = config.leggi("email.avvisi_server") or []
        if not destinatari:
            self.stderr.write("Nessun destinatario: Configurazione → Indirizzi email → Destinatari avvisi del server.")
            raise SystemExit(3)
        testo = sys.stdin.read()[:20000]
        nome = config.leggi("azienda.nome_fornitore") or "Metodo v2"
        mittente = config.leggi("email.mittente") or settings.DEFAULT_FROM_EMAIL
        send_mail(f"[Metodo v2 {nome}] {opzioni['oggetto'][:150]}", testo, mittente, destinatari)
        self.stdout.write(f"Avviso inviato a {len(destinatari)} destinatari.")
