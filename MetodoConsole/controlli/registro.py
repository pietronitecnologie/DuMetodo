"""Le aree di controllo: nome, titolo, quando si attivano e cosa controllano."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from . import database, django, docker, posta, sicurezza, sistema, web
from .base import ERRORE, NOMI_GRUPPI, Contesto, Esito


@dataclass(frozen=True)
class Area:
    nome: str
    descrizione: str
    presente: Callable[[Contesto], bool]
    controlli: Callable[[Contesto], list[Esito]]

    @property
    def titolo(self) -> str:
        return NOMI_GRUPPI.get(self.nome, self.nome)


AREE: dict[str, Area] = {
    a.nome: a
    for a in (
        Area(
            "sistema",
            "versione e fine supporto, aggiornamenti, CPU, memoria, dischi, servizi, orologio, log",
            sistema.presente_area,
            sistema.controlli,
        ),
        Area(
            "sicurezza",
            "firewall, porte esposte, SSH, fail2ban, utenti, sudo, aggiornamenti automatici",
            sicurezza.presente_area,
            sicurezza.controlli,
        ),
        Area(
            "web",
            "nginx/Apache, siti, redirect https, intestazioni, certificati e rinnovo, errori 5xx",
            web.presente_area,
            web.controlli,
        ),
        Area(
            "posta",
            "Postfix/Exim/Dovecot, coda, relay aperto, TLS, log, DNS (MX, SPF, DMARC, DKIM), PTR, liste nere",
            posta.presente_area,
            posta.controlli,
        ),
        Area(
            "database",
            "PostgreSQL, MySQL/MariaDB e Redis installati sul server (non in container)",
            database.presente_area,
            database.controlli,
        ),
        Area(
            "django",
            "applicazioni Django con gunicorn/uwsgi: servizio, check --deploy, migrazioni, versioni",
            django.presente_area,
            django.controlli,
        ),
        Area(
            "docker",
            "demone, container non sani o fermati con errore, spazio recuperabile",
            docker.presente_area,
            docker.controlli,
        ),
    )
}


def rilevate(ctx: Contesto) -> list[str]:
    """Le aree che hanno senso su questo server (o quelle scelte in [diagnostica] aree)."""
    scelte = ctx.lista("diagnostica", "aree", "auto")
    if scelte and scelte != ["auto"]:
        return [a for a in scelte if a in AREE]
    return [n for n, a in AREE.items() if _sicuro(lambda a=a: a.presente(ctx), False)]


def _sicuro(funzione: Callable[[], object], predefinito: object) -> object:
    try:
        return funzione()
    except Exception:
        return predefinito


def esegui_aree(ctx: Contesto, aree: Iterable[str] | None = None) -> list[Esito]:
    """Esegue le aree indicate (o tutte quelle rilevate). Un controllo che si rompe diventa un esito, non un crash."""
    nomi = list(aree) if aree else rilevate(ctx)
    esiti: list[Esito] = []
    for nome in nomi:
        area = AREE[nome]
        try:
            esiti += area.controlli(ctx)
        except Exception as exc:  # un'area rotta non deve fermare le altre
            esiti.append(Esito(f"Controlli {area.titolo}", ERRORE, f"errore interno di MetodoConsole: {exc!r}", nome))
    return esiti
