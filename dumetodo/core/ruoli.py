"""Ruoli applicativi e controlli di accesso.

Il "Fornitore" è l'azienda che possiede DuMetodo e produce; il "Cliente" è chi invia gli ordini.

In Metodo i permessi erano controllati con copie sparse di funzioni per gruppo e molti
controlli erano solo nei template. Qui c'è un'unica fonte di verità, con gerarchia
esplicita: un Amministratore è anche Caporeparto e Operatore. I vecchi gruppi di Metodo
vengono rinominati automaticamente (vedi ``GRUPPI_METODO`` e la migrazione core 0002).
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from functools import wraps
from typing import Any

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse


class Ruolo(StrEnum):
    OPERATORE = "Operatore"
    CAPOREPARTO = "Caporeparto"
    AMMINISTRATORE = "Amministratore"
    CLIENTE = "Cliente"


# Nome del gruppo in Metodo -> ruolo in DuMetodo (usato dalla migrazione degli utenti).
GRUPPI_METODO: dict[str, Ruolo] = {
    "Geko": Ruolo.OPERATORE,
    "GekoCapRep": Ruolo.CAPOREPARTO,
    "Geko Admin": Ruolo.AMMINISTRATORE,
    "Customer": Ruolo.CLIENTE,
}


# Ruoli impliciti: chi ha la chiave ha anche i ruoli elencati.
_IMPLICA: dict[Ruolo, set[Ruolo]] = {
    Ruolo.AMMINISTRATORE: {Ruolo.OPERATORE, Ruolo.CAPOREPARTO},
    Ruolo.CAPOREPARTO: {Ruolo.OPERATORE},
}


def ruoli_utente(user: Any) -> frozenset[Ruolo]:
    """Ruoli effettivi dell'utente (con gerarchia). Memorizzati sull'oggetto user per la richiesta."""
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return frozenset()
    cached = getattr(user, "_ruoli_cache", None)
    if cached is not None:
        return cached
    nomi = set(user.groups.values_list("name", flat=True))
    ruoli: set[Ruolo] = {r for r in Ruolo if r.value in nomi}
    if user.is_superuser:
        ruoli |= {Ruolo.AMMINISTRATORE}
    for r in list(ruoli):
        ruoli |= _IMPLICA.get(r, set())
    result = frozenset(ruoli)
    user._ruoli_cache = result
    return result


def ha_ruolo(user: Any, *ruoli: Ruolo) -> bool:
    return bool(ruoli_utente(user) & set(ruoli))


def is_fornitore(user: Any) -> bool:
    """Personale del Fornitore (operatore, caporeparto o amministratore)."""
    return ha_ruolo(user, Ruolo.OPERATORE)


def richiede_ruolo(*ruoli: Ruolo) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    """Decoratore per viste a funzione: 302 al login se anonimo, 403 se manca il ruolo."""

    def decorator(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @wraps(view)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login

                return redirect_to_login(request.get_full_path())
            if not ha_ruolo(request.user, *ruoli):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


class RuoloRichiestoMixin(AccessMixin):
    ruoli_richiesti: tuple[Ruolo, ...] = ()

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not ha_ruolo(request.user, *self.ruoli_richiesti):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]
