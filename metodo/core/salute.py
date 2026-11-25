"""Controllo di salute per Docker e MetodoConsole: GET /salute/ -> 200 se database e cache rispondono.

È un middleware in testa alla catena: risponde prima del controllo dell'host e del redirect https, così
funziona anche chiamato dall'interno del container (http://127.0.0.1:8000/salute/). Non espone dettagli:
solo quali componenti non rispondono.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse

PERCORSO = "/salute/"


def controlla() -> dict[str, bool]:
    from django.core.cache import cache
    from django.db import connection

    esiti: dict[str, bool] = {}
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
            esiti["database"] = c.fetchone() == (1,)
    except Exception:
        esiti["database"] = False
    try:
        chiave, valore = "metodo:salute", uuid.uuid4().hex
        cache.set(chiave, valore, 30)
        esiti["cache"] = cache.get(chiave) == valore
    except Exception:
        esiti["cache"] = False
    return esiti


class SaluteMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path != PERCORSO:
            return self.get_response(request)
        from .versione import versione

        esiti = controlla()
        ok = all(esiti.values())
        r = JsonResponse(
            {"stato": "ok" if ok else "errore", "componenti": esiti, "versione": versione()},
            status=200 if ok else 503,
        )
        r["Cache-Control"] = "no-store"
        return r
