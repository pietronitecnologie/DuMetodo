"""Diagnostica dell'applicazione (dall'interno del container): usata da ``manage.py diagnostica`` e da MetodoConsole.

Ogni controllo restituisce un Esito: ``ok``, ``attenzione`` (da guardare) o ``errore`` (qualcosa non funziona).
Nessun controllo modifica dati; i messaggi non contengono password né dati personali.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

OK, ATTENZIONE, ERRORE = "ok", "attenzione", "errore"
GRAVITA = {OK: 0, ATTENZIONE: 1, ERRORE: 2}


@dataclass
class Esito:
    nome: str
    esito: str
    dettaglio: str = ""

    def come_dict(self) -> dict[str, str]:
        return asdict(self)


CONTROLLI: list[Callable[[], Esito]] = []


def controllo(f: Callable[[], Esito]) -> Callable[[], Esito]:
    CONTROLLI.append(f)
    return f


def esegui() -> list[Esito]:
    esiti = []
    for f in CONTROLLI:
        try:
            esiti.append(f())
        except Exception as exc:  # un controllo che fallisce è esso stesso un errore da mostrare
            esiti.append(
                Esito(f.__name__.replace("_", " "), ERRORE, f"controllo non riuscito: {exc.__class__.__name__}: {exc}")
            )
    return esiti


def peggiore(esiti: list[Esito]) -> str:
    return max((e.esito for e in esiti), key=GRAVITA.__getitem__, default=OK)


def _mb(n: float) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GB".replace(".", ",")
    return f"{n / (1 << 20):.0f} MB"


# ------------------------------------------------------------------------------------------------ controlli
@controllo
def database() -> Esito:
    from django.db import connection

    with connection.cursor() as c:
        c.execute("SELECT 1")
        if connection.vendor != "postgresql":
            return Esito("Database", ATTENZIONE, f"{connection.vendor}: in produzione si usa PostgreSQL")
        c.execute("SELECT version(), pg_database_size(current_database())")
        versione, dimensione = c.fetchone()
        c.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
        (connessioni,) = c.fetchone()
        c.execute("SHOW max_connections")
        (massimo,) = c.fetchone()
    esito = ATTENZIONE if connessioni > int(massimo) * 0.8 else OK
    return Esito(
        "Database",
        esito,
        f"{versione.split(' on ')[0]}, {_mb(dimensione)}, {connessioni}/{massimo} connessioni",
    )


@controllo
def migrazioni() -> Esito:
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    piano = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if piano:
        nomi = ", ".join(f"{m.app_label}.{m.name}" for m, _ in piano[:5])
        return Esito("Migrazioni", ERRORE, f"{len(piano)} da applicare: {nomi}")
    return Esito("Migrazioni", OK, "database allineato al codice")


@controllo
def cache() -> Esito:
    from .salute import controlla

    if not controlla()["cache"]:
        return Esito("Cache (Redis)", ERRORE, "la cache non risponde")
    from django.conf import settings

    backend = settings.CACHES["default"]["BACKEND"].rsplit(".", 1)[-1]
    esito = OK if "Redis" in backend else ATTENZIONE
    return Esito("Cache (Redis)", esito, backend if esito == OK else f"{backend}: in produzione si usa Redis")


@controllo
def worker_celery() -> Esito:
    from django.conf import settings

    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return Esito("Worker Celery", OK, "lavori eseguiti subito (installazione semplice)")
    from config.celery import app

    risposte = app.control.ping(timeout=3) or []
    if not risposte:
        return Esito("Worker Celery", ERRORE, "nessun worker risponde: email e lavori periodici sono fermi")
    return Esito("Worker Celery", OK, f"{len(risposte)} worker attivi")


@controllo
def lavori_periodici() -> Esito:
    from django.utils import timezone
    from django_celery_beat.models import PeriodicTask

    attivi = PeriodicTask.objects.filter(enabled=True).exclude(name__startswith="celery.")
    if not attivi.exists():
        return Esito(
            "Lavori periodici", ATTENZIONE, "nessun lavoro periodico attivo (manage.py configura_pianificazioni)"
        )
    ultimo = attivi.exclude(last_run_at=None).order_by("-last_run_at").values_list("last_run_at", flat=True).first()
    if ultimo is None:
        return Esito("Lavori periodici", ATTENZIONE, f"{attivi.count()} attivi, nessuno ancora eseguito")
    if timezone.now() - ultimo > timedelta(hours=2):
        return Esito(
            "Lavori periodici", ERRORE, f"ultimo eseguito il {timezone.localtime(ultimo):%d/%m %H:%M}: beat fermo?"
        )
    return Esito("Lavori periodici", OK, f"{attivi.count()} attivi, ultimo alle {timezone.localtime(ultimo):%H:%M}")


@controllo
def casella_ordini() -> Esito:
    from django.utils import timezone

    from metodo.importazione.models import StatoCasella
    from metodo.importazione.posta import casella_configurata

    if not casella_configurata():
        return Esito("Casella ordini", OK, "non configurata")
    stato = StatoCasella.objects.order_by("-ultima_lettura_il").first()
    if stato is None or stato.ultima_lettura_il is None:
        return Esito("Casella ordini", ATTENZIONE, "mai letta")
    if stato.ultimo_errore:
        return Esito("Casella ordini", ERRORE, f"ultimo errore: {stato.ultimo_errore[:200]}")
    if timezone.now() - stato.ultima_lettura_il > timedelta(hours=2):
        return Esito(
            "Casella ordini", ATTENZIONE, f"ultima lettura {timezone.localtime(stato.ultima_lettura_il):%d/%m %H:%M}"
        )
    return Esito("Casella ordini", OK, f"letta alle {timezone.localtime(stato.ultima_lettura_il):%H:%M}")


@controllo
def email_in_uscita() -> Esito:
    from django.conf import settings
    from django.utils import timezone

    from metodo.comunicazioni.models import EmailInUscita, StatoEmail

    errori = EmailInUscita.objects.filter(stato=StatoEmail.ERRORE).count()
    ferme = EmailInUscita.objects.filter(
        stato=StatoEmail.IN_CODA, creata_il__lt=timezone.now() - timedelta(minutes=30)
    ).count()
    if errori or ferme:
        return Esito("Email in uscita", ATTENZIONE, f"{errori} in errore, {ferme} in coda da oltre 30 minuti")
    if ".smtp." not in settings.EMAIL_BACKEND:
        return Esito(
            "Email in uscita", ATTENZIONE, f"invio non configurato ({settings.EMAIL_BACKEND.rsplit('.', 2)[-2]})"
        )
    return Esito("Email in uscita", OK, f"SMTP {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")


@controllo
def problemi_importazione() -> Esito:
    from metodo.importazione.models import ProblemaImportazione

    n = ProblemaImportazione.objects.filter(gestito=False).count()
    return Esito("Problemi di importazione", ATTENZIONE if n else OK, f"{n} da gestire" if n else "nessuno")


@controllo
def file_caricati() -> Esito:
    from django.conf import settings

    cartella = str(settings.MEDIA_ROOT)
    if not os.path.isdir(cartella):
        return Esito("Cartella file", ERRORE, "cartella dei file caricati mancante")
    if not os.access(cartella, os.W_OK):
        return Esito("Cartella file", ERRORE, "non scrivibile: foto e allegati non si possono salvare")
    spazio = shutil.disk_usage(cartella)
    libero = spazio.free / spazio.total
    esito = ERRORE if libero < 0.05 else ATTENZIONE if libero < 0.15 else OK
    return Esito("Cartella file", esito, f"spazio libero {_mb(spazio.free)} ({libero:.0%})")


@controllo
def sicurezza() -> Esito:
    from django.conf import settings
    from django.contrib.auth.models import User

    problemi = []
    if settings.DEBUG:
        problemi.append("DEBUG attivo")
    if len(settings.SECRET_KEY) < 40 or "cambia" in settings.SECRET_KEY:
        problemi.append("DJANGO_SECRET_KEY debole o di esempio")
    if not getattr(settings, "SESSION_COOKIE_SECURE", False):
        problemi.append("https disattivato (DJANGO_HTTPS=false)")
    if not User.objects.filter(is_superuser=True, is_active=True).exists():
        problemi.append("nessun amministratore attivo")
    if problemi:
        grave = any(p.startswith(("DEBUG", "DJANGO_SECRET")) for p in problemi)
        return Esito("Sicurezza", ERRORE if grave else ATTENZIONE, "; ".join(problemi))
    return Esito("Sicurezza", OK, "configurazione di produzione")


def riepilogo() -> dict[str, Any]:
    from .versione import versione

    esiti = esegui()
    return {"versione": versione(), "esito": peggiore(esiti), "controlli": [e.come_dict() for e in esiti]}
