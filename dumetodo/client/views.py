import json

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from dumetodo.configurazione import servizi as config

from . import menu


@require_GET
@login_required
def configurazione(request: HttpRequest) -> JsonResponse:
    """Menu del tasto destro per l'utente collegato (letti da client.js nella pagina)."""
    try:
        dati = menu.valida(config.leggi("client.menu"))
    except menu.MenuNonValido:
        dati = menu.PREDEFINITO
    r = JsonResponse({"versione": 1, "menu": menu.per_utente(dati, request.user)})
    r["Cache-Control"] = "private, no-store"
    return r


def predefinito_json() -> str:
    return json.dumps(menu.PREDEFINITO, ensure_ascii=False, indent=2)
