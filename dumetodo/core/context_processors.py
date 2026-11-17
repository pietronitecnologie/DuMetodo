from django.http import HttpRequest

from .ruoli import Ruolo, ruoli_utente


def ruoli(request: HttpRequest) -> dict[str, object]:
    r = ruoli_utente(request.user)
    return {
        "ruoli": r,
        "is_operatore": Ruolo.OPERATORE in r,
        "is_amministratore": Ruolo.AMMINISTRATORE in r,
        "is_caporeparto": Ruolo.CAPOREPARTO in r,
        "is_cliente": Ruolo.CLIENTE in r,
    }


def azienda(request: HttpRequest) -> dict[str, object]:
    from dumetodo.configurazione import servizi as config

    return {
        "nome_fornitore": config.leggi("azienda.nome_fornitore") or "Fornitore",
        "nome_cliente": config.leggi("azienda.nome_cliente") or "Cliente",
    }
