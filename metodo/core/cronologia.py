"""Pulsante «indietro» comune a tutte le pagine: porta alla pagina visitata prima, con i filtri com'erano.

Il middleware tiene in sessione le ultime pagine aperte (indirizzo completo e titolo), come la cronologia del
browser:
- riaprire la pagina in cima (altri filtri, ricaricamento) la aggiorna;
- dopo un salvataggio (POST con rimando) si torna alla pagina già nella pila e quelle dopo si tolgono;
- il pulsante passa da /indietro/, che toglie la pagina corrente e rimanda alla precedente;
- le richieste HTMX sulla stessa pagina (i filtri che cambiano l'indirizzo senza ricaricare) aggiornano
  l'indirizzo della pagina in cima.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Callable
from urllib.parse import urlsplit

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET

CHIAVE = "cronologia_pagine"
DOPO_POST = "cronologia_dopo_post"
MASSIMO = 30
ESCLUSI = ("/static/", "/media/", "/accesso/", "/uscita/", "/salute/", "/chat/", "/timbrature/p/", "/favicon.ico")
log = logging.getLogger(__name__)
_TITOLO = re.compile(r"<title>\s*(.*?)\s*</title>", re.S | re.I)


def _percorso(url: str) -> str:
    return urlsplit(url).path


def aggiorna(storia: list[dict[str, str]], url: str, titolo: str = "", ritorno: bool = False) -> list[dict[str, str]]:
    """La pila dopo aver aperto ``url``; con ``ritorno`` (dopo un salvataggio o con «indietro») si torna alla pagina
    se è già nella pila, togliendo quelle aperte dopo."""
    storia = list(storia)
    percorso = _percorso(url)
    if ritorno:
        for i in range(len(storia) - 1, -1, -1):
            if _percorso(storia[i]["url"]) == percorso:
                storia = storia[: i + 1]
                break
    if storia and _percorso(storia[-1]["url"]) == percorso:
        storia[-1] = {"url": url, "titolo": titolo or storia[-1].get("titolo", "")}
    else:
        storia.append({"url": url, "titolo": titolo})
    return storia[-MASSIMO:]


def precedente(request: HttpRequest) -> dict[str, str] | None:
    """La pagina a cui porta «indietro» dalla pagina corrente (None se non ce n'è una)."""
    sessione = getattr(request, "session", None)
    if sessione is None:
        return None
    storia = aggiorna(sessione.get(CHIAVE) or [], request.get_full_path(), ritorno=bool(sessione.get(DOPO_POST)))
    return storia[-2] if len(storia) >= 2 else None


def url_sicuro(url: str) -> bool:
    """Solo indirizzi di questo sito («/…», non «//altro.sito»)."""
    return url.startswith("/") and not url.startswith("//") and "\\" not in url


@login_required
@require_GET
def indietro(request: HttpRequest) -> HttpResponse:
    """Il pulsante «indietro»: torna alla pagina ``a`` togliendo dalla pila quelle aperte dopo."""
    dest = request.GET.get("a", "")
    if not url_sicuro(dest):
        return redirect("/")
    request.session[DOPO_POST] = True  # all'arrivo, la pila si accorcia fino a quella pagina
    return redirect(dest)


def _titolo(risposta: HttpResponse) -> str:
    m = _TITOLO.search(risposta.content[:4000].decode("utf-8", "replace"))
    # «Ordine 123 · Metodo v2 · Fornitore» → «Ordine 123»
    return html.unescape(m.group(1)).split(" · ")[0].strip()[:80] if m else ""


class CronologiaMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        risposta = self.get_response(request)
        try:
            self._registra(request, risposta)
        except Exception:  # la cronologia non deve mai rompere una pagina
            log.warning("Cronologia delle pagine non aggiornata", exc_info=True)
        return risposta

    @staticmethod
    def _registra(request: HttpRequest, risposta: HttpResponse) -> None:
        if request.method == "POST" and 300 <= risposta.status_code < 400 and hasattr(request, "session"):
            request.session[DOPO_POST] = True
            return
        if request.method != "GET" or risposta.status_code != 200 or getattr(risposta, "streaming", False):
            return
        if not getattr(getattr(request, "user", None), "is_authenticated", False):
            return
        if request.path.startswith(ESCLUSI) or "text/html" not in risposta.get("Content-Type", ""):
            return
        url = request.get_full_path()[:2000]
        storia = request.session.get(CHIAVE) or []
        if request.headers.get("HX-Request"):
            # Un pezzo di pagina: aggiorna solo l'indirizzo della pagina in cima, se è la stessa (filtri HTMX).
            if storia and _percorso(storia[-1]["url"]) == request.path and request.headers.get("HX-Target"):
                storia[-1] = {**storia[-1], "url": url}
                request.session[CHIAVE] = storia
            return
        ritorno = bool(request.session.pop(DOPO_POST, False))
        nuova = aggiorna(storia, url, _titolo(risposta), ritorno)
        if nuova != storia:
            request.session[CHIAVE] = nuova
