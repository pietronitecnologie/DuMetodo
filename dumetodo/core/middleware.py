"""Chi ha una password temporanea (generata da un amministratore) deve cambiarla prima di usare DuMetodo."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
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
                    if request.headers.get("HX-Request"):
                        r = HttpResponse(status=204)
                        r["HX-Redirect"] = pagina
                        return r
                    return redirect(pagina)
        return self.get_response(request)
