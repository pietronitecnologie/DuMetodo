from django.http import HttpRequest

from .permessi import is_cliente, is_fornitore


def ruoli(request: HttpRequest) -> dict[str, object]:
    """Identità dell'utente; i permessi nei template si leggono con ``perms.core.<funzione>``."""
    return {"is_cliente": is_cliente(request.user), "is_fornitore": is_fornitore(request.user)}


def azienda(request: HttpRequest) -> dict[str, object]:
    from dumetodo.configurazione import servizi as config

    return {
        "nome_fornitore": config.leggi("azienda.nome_fornitore") or "Fornitore",
        "nome_cliente": config.leggi("azienda.nome_cliente") or "Cliente",
    }
