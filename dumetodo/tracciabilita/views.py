"""Archivio delle tracce (in sola lettura): le nuove registrazioni, i controlli qualità e le foto si fanno con
il modulo Tracciatura."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from dumetodo.configurazione import servizi as config
from dumetodo.core.permessi import F, is_cliente, richiede_permesso
from dumetodo.ordini.models import Ordine

from . import forms, servizi
from .models import Traccia

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PER_PAGINA = 50


def _xlsx(nome: str, contenuto: bytes) -> HttpResponse:
    r = HttpResponse(contenuto, content_type=XLSX)
    r["Content-Disposition"] = f'attachment; filename="{nome}"'
    return r


def _giorni_fa(giorni: int) -> dict[str, str]:
    oggi = timezone.localdate()
    return {"dal": (oggi - dt.timedelta(days=giorni)).isoformat(), "al": oggi.isoformat()}


def _lista(
    request: HttpRequest,
    *,
    form_cls: type[forms.Filtri],
    qs: QuerySet[Any],
    template: str,
    esporta: Callable[[QuerySet[Any]], bytes],
    nome_file: str,
    predefiniti: dict[str, str] | None = None,
    contesto: dict[str, Any] | None = None,
) -> HttpResponse:
    """Lista con filtri in GET, paginazione ed esportazione xlsx degli STESSI dati filtrati."""
    filtri = [k for k in request.GET if k not in ("pagina", "formato")]
    form = form_cls(request.GET if filtri else (predefiniti or {}))
    qs = form.filtra(qs) if form.is_valid() else qs.none()
    if request.GET.get("formato") == "xlsx":
        return _xlsx(nome_file, esporta(qs))
    pagina = Paginator(qs, PER_PAGINA).get_page(request.GET.get("pagina"))
    return render(request, template, {"form": form, "pagina": pagina, **(contesto or {})})


# =========================================================================== tracce
def _tracce() -> QuerySet[Traccia]:
    return Traccia.objects.select_related("elemento", "ordine", "reparto", "inserita_da")


@require_GET
@richiede_permesso(F.TRACCIABILITA_CONSULTA)
def tracce(request: HttpRequest) -> HttpResponse:
    qs = _tracce()
    if is_cliente(request.user):
        qs = qs.filter(ordine__isnull=False)
    return _lista(
        request,
        form_cls=forms.FiltriTracce,
        qs=qs,
        template="tracciabilita/tracce.html",
        esporta=servizi.esporta_tracce,
        nome_file="ListaTracce.xlsx",
        predefiniti=_giorni_fa(config.leggi("tracciabilita.giorni_liste")),
    )


@require_GET
@richiede_permesso(F.TRACCIABILITA_CONSULTA)
def traccia(request: HttpRequest, pk: int) -> HttpResponse:
    t = get_object_or_404(_tracce(), pk=pk)
    if is_cliente(request.user) and not t.ordine_id:
        raise Http404  # il cliente vede solo le tracce dei suoi ordini
    storia = _tracce().filter(elemento=t.elemento) if t.elemento_id else _tracce().filter(pk=t.pk)
    return render(request, "tracciabilita/traccia.html", {"traccia": t, "storia": storia[:100]})


@require_GET
@richiede_permesso(F.TRACCIABILITA_CONSULTA)
def stampa(request: HttpRequest) -> HttpResponse:
    """Pagina da stampare con il codice a barre Code128 (generato nel browser con JsBarcode)."""
    testo = request.GET.get("barcode", "").strip()
    if not testo or len(testo) > 200 or not testo.isprintable() or not testo.isascii():
        return HttpResponseBadRequest("Parametro barcode mancante o non valido (testo ASCII, max 200 caratteri).")
    return render(request, "tracciabilita/stampa.html", {"barcode": testo})


@require_GET
@richiede_permesso(F.TRACCIABILITA_CONSULTA)
def pannello_ordine(request: HttpRequest, ordine_id: int) -> HttpResponse:
    """Tracce dell'ordine nell'archivio (solo se ce ne sono)."""
    ordine = get_object_or_404(Ordine, pk=ordine_id)
    return render(
        request,
        "tracciabilita/_pannello_ordine.html",
        {"ordine": ordine, "tracce": _tracce().filter(ordine=ordine)[:100]},
    )
