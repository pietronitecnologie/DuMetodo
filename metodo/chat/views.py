"""Chat: pannello (pagina stretta mostrata dal client a fianco delle schede) e API JSON per il pannello."""

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from metodo.core.permessi import F, richiede_permesso

from . import servizi

# Pannello chat prima dell'accesso. Il client desktop lo tiene aperto, nascosto, insieme alla scheda visibile.
# Se qui comparisse la pagina di accesso, le due pagine creerebbero due cookie CSRF diversi nello stesso momento e
# il login nella scheda visibile potrebbe fallire («CSRF token from POST incorrect»). Questa pagina non crea
# cookie e si ricarica da sola: appena l'utente entra in una scheda, mostra la chat.
IN_ATTESA = (
    '<!doctype html><html lang="it"><head><meta charset="utf-8">'
    '<meta http-equiv="refresh" content="5"><title>Chat</title></head>'
    '<body style="font-family:system-ui,sans-serif;color:#5b6573;padding:1rem">'
    "La chat si apre dopo l'accesso.</body></html>"
)


@require_GET
def pannello(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        r = HttpResponse(IN_ATTESA)
        r["Cache-Control"] = "no-store"
        return r
    return _pannello(request)


@richiede_permesso(F.CHAT)
def _pannello(request: HttpRequest) -> HttpResponse:
    return render(request, "chat/pannello.html")


@require_GET
@richiede_permesso(F.CHAT)
def stato(request: HttpRequest) -> JsonResponse:
    r = JsonResponse(servizi.stato(request.user))
    r["Cache-Control"] = "private, no-store"
    return r


def _id(request: HttpRequest, nome: str) -> int:
    valore = request.POST.get(nome, "")
    return int(valore) if valore.isdigit() else 0


@require_POST
@richiede_permesso(F.CHAT)
def invia(request: HttpRequest) -> JsonResponse:
    try:
        m = servizi.invia(request.user, _id(request, "a"), request.POST.get("testo", ""))
    except servizi.ChatNonConsentita as exc:
        return JsonResponse({"errore": str(exc)}, status=400)
    return JsonResponse({"id": m.pk})


@require_POST
@richiede_permesso(F.CHAT)
def letti(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"letti": servizi.segna_letti(request.user, _id(request, "con"))})


@require_POST
@richiede_permesso(F.CHAT)
def chiudi(request: HttpRequest) -> JsonResponse:
    servizi.chiudi(request.user, _id(request, "con"))
    return JsonResponse({"ok": True})


@require_POST
@richiede_permesso(F.CHAT)
def riapri(request: HttpRequest) -> JsonResponse:
    servizi.riapri(request.user, _id(request, "con"))
    return JsonResponse({"ok": True})
