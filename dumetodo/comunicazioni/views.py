from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from dumetodo.core.permessi import F, is_fornitore, richiede_permesso
from dumetodo.ordini.models import Ordine

from . import servizi
from .models import Nota, Visibilita


def _pannello(request: HttpRequest, ordine: Ordine, errore: str = "", testo: str = "") -> HttpResponse:
    return render(
        request,
        "comunicazioni/_pannello_note.html",
        {
            "ordine": ordine,
            "note": servizi.note_visibili(ordine, request.user),
            "errore": errore,
            "testo": testo,
        },
    )


@require_GET
@richiede_permesso(F.ORDINI_VEDERE)
def pannello(request: HttpRequest, ordine_id: int) -> HttpResponse:
    return _pannello(request, get_object_or_404(Ordine, pk=ordine_id))


@require_POST
@richiede_permesso(F.NOTE_SCRIVERE)
def aggiungi(request: HttpRequest, ordine_id: int) -> HttpResponse:
    ordine = get_object_or_404(Ordine, pk=ordine_id)
    testo = request.POST.get("testo", "")
    visibilita = request.POST.get("visibilita", Visibilita.CONDIVISA)
    try:
        servizi.aggiungi_nota(ordine, request.user, testo, visibilita)
    except servizi.NotaNonValida as exc:
        return _pannello(request, ordine, errore=str(exc), testo=testo)
    return _pannello(request, ordine)


@require_POST
@richiede_permesso(F.NOTE_SCRIVERE)
def elimina(request: HttpRequest, nota_id: int) -> HttpResponse:
    qs = Nota.objects.select_related("ordine")
    if not is_fornitore(request.user):
        qs = qs.filter(visibilita=Visibilita.CONDIVISA)
    nota = get_object_or_404(qs, pk=nota_id)
    errore = ""
    try:
        servizi.elimina_nota(nota, request.user)
    except servizi.NotaNonValida as exc:
        errore = str(exc)
    return _pannello(request, nota.ordine, errore=errore)
