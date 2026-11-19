"""Chi ha una password temporanea (generata da un amministratore) deve cambiarla prima di usare DuMetodo."""

from collections.abc import Callable
from urllib.parse import urlsplit

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

LIBERI = ("/static/", "/favicon.ico", "/uscita/", "/configurazione/logo.png")


class PasswordDaCambiareMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        utente = getattr(request, "user", None)
        if utente is not None and utente.is_authenticated:
            pagina = reverse("utenti:mia_password")
            if request.path != pagina and not request.path.startswith(LIBERI):
                profilo = getattr(utente, "profilo_dumetodo", None)
                try:
                    obbligo = bool(profilo and profilo.deve_cambiare_password)
                except Exception:  # profilo non ancora creato
                    obbligo = False
                if obbligo:
                    return _rimanda(request, pagina)
        return self.get_response(request)


def _rimanda(request: HttpRequest, pagina: str) -> HttpResponse:
    """Porta l'utente alla pagina del cambio password senza creare cicli di ricarica.

    Le richieste htmx partite DALLA pagina del cambio password (es. gli annunci caricati da base.html)
    ricevono una risposta vuota: rimandarle a quella stessa pagina la ricaricherebbe all'infinito,
    cancellando ciò che l'utente sta scrivendo. Le richieste di dati (fetch del client, chat) ricevono
    un errore, non una pagina HTML.
    """
    if request.headers.get("HX-Request"):
        r = HttpResponse(status=204)
        if urlsplit(request.headers.get("HX-Current-URL", "")).path != pagina:
            r["HX-Redirect"] = pagina
        return r
    if "text/html" not in request.headers.get("Accept", "text/html"):
        return JsonResponse({"errore": "password_da_cambiare"}, status=403)
    return redirect(pagina)
