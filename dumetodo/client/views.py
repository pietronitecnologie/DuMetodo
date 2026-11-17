import base64
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
    r = JsonResponse({"versione": 1, "menu": menu.per_utente(dati, request.user), "intestazione": intestazione_menu()})
    r["Cache-Control"] = "private, no-store"
    return r


def intestazione_menu() -> dict[str, object] | None:
    """Logo e nome dell'azienda in testa al menu nativo (icone già alle misure dei menu: 16/24/32 px)."""
    logo = config.leggi("azienda.logo")
    if not logo:
        return None
    from dumetodo.configurazione.immagini import png_quadrato

    return {
        "titolo": config.leggi("azienda.nome_fornitore") or "",
        "loghi": {str(lato): base64.b64encode(png_quadrato(logo, lato)).decode() for lato in (16, 24, 32)},
    }


def predefinito_json() -> str:
    return json.dumps(menu.PREDEFINITO, ensure_ascii=False, indent=2)
