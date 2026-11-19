import base64
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from dumetodo.configurazione import servizi as config
from dumetodo.core.permessi import F, richiede_permesso

from . import aggiornamenti, menu, scorciatoie
from .models import VersioneClient


@require_GET
@login_required
def configurazione(request: HttpRequest) -> JsonResponse:
    """Menu del tasto destro per l'utente collegato (letti da client.js nella pagina)."""
    try:
        dati = menu.valida(config.leggi("client.menu"))
    except menu.MenuNonValido:
        dati = menu.PREDEFINITO
    try:
        tasti = scorciatoie.valida(config.leggi("client.scorciatoie"))
    except scorciatoie.ScorciatoieNonValide:
        tasti = scorciatoie.PREDEFINITE
    r = JsonResponse(
        {
            "versione": 1,
            "menu": menu.per_utente(dati, request.user),
            "intestazione": intestazione_menu(),
            "scorciatoie": scorciatoie.per_utente(tasti, request.user),
        }
    )
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


# ----------------------------------------------------------------------------------------- aggiornamenti
@require_GET
@login_required
def aggiornamento(request: HttpRequest) -> JsonResponse:
    """Ultima versione proposta ai client (client.js la confronta con quella in uso)."""
    v = aggiornamenti.ultima()
    dati = None
    if v is not None:
        dati = {
            "versione": v.versione,
            "sha256": v.sha256,
            "dimensione": v.dimensione,
            "note": v.note,
            "url": reverse("client:scarica", args=[v.pk]),
        }
    r = JsonResponse({"aggiornamento": dati})
    r["Cache-Control"] = "private, no-store"
    return r


@require_GET
@login_required
def scarica(request: HttpRequest, pk: int) -> FileResponse:
    v = get_object_or_404(VersioneClient, pk=pk, attiva=True)
    return FileResponse(v.file.open("rb"), as_attachment=True, filename=f"dumetodo-client-{v.versione}.exe")


@richiede_permesso(F.CONFIGURAZIONE)
def versioni(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        azione = request.POST.get("azione", "carica")
        if azione == "carica":
            try:
                p, file = request.POST, request.FILES.get("file")
                v = aggiornamenti.carica(request.user, p.get("versione", ""), file, p.get("note", ""))
            except aggiornamenti.AggiornamentoNonValido as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Client {v.versione} caricato: i client lo proporranno agli utenti.")
                return redirect("client:versioni")
        elif azione in ("attiva", "disattiva"):
            VersioneClient.objects.filter(pk=request.POST.get("pk")).update(attiva=azione == "attiva")
            return redirect("client:versioni")
    return render(
        request,
        "client/versioni.html",
        {
            "versioni": sorted(VersioneClient.objects.all(), key=lambda v: v.numeri, reverse=True),
            "ultima": aggiornamenti.ultima(),
        },
    )
