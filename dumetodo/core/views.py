from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from .permessi import F, ha_permesso

# Prima pagina accessibile, in ordine di priorità.
PAGINE_INIZIALI = (
    (F.ORDINI_VEDERE, "ordini:lista"),
    (F.REPORT_DASHBOARD, "report:dashboard"),
    (F.AGENDA, "pianificazione:agenda"),
    (F.PIANIFICAZIONE, "pianificazione:pagina"),
    (F.CONSEGNA, "consegne:pagina"),
    (F.MAGAZZINO_RICHIESTE, "magazzino:richieste"),
    (F.TRACCIABILITA_CONSULTA, "tracciabilita:batterie"),
    (F.UTILITA_ANNUNCI, "utilita:indice"),
    (F.CONFIGURAZIONE, "configurazione:pagina"),
    (F.UTENTI, "utenti:lista"),
)


@login_required
def home(request: HttpRequest) -> HttpResponse:
    for codice, pagina in PAGINE_INIZIALI:
        if ha_permesso(request.user, codice):
            return redirect(pagina)
    return render(request, "nessun_permesso.html", status=403)
