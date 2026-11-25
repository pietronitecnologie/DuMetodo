"""Chat: pannello (pagina stretta mostrata dal client a fianco delle schede) e API JSON per il pannello."""

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from metodo.core.permessi import F, richiede_permesso

from . import servizi


@require_GET
@richiede_permesso(F.CHAT)
def pannello(request: HttpRequest) -> HttpResponse:
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
