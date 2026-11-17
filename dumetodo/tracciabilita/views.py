"""Viste di tracciabilità e qualità: sottili, chiamano ``servizi``. Ogni modifica è POST con CSRF."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Prefetch, QuerySet
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from dumetodo.configurazione import servizi as config
from dumetodo.core.permessi import F, is_cliente, richiede_permesso
from dumetodo.ordini.models import Ordine

from . import forms, servizi
from .models import (
    EsitoControllo,
    FileOrdine,
    Test,
    TipoControllo,
    Traccia,
)
from .servizi import OperazioneNonConsentita

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


def _errori_form(request: HttpRequest, form: Any) -> None:
    for errore in form.non_field_errors():
        messages.error(request, errore)


# =========================================================================== tracce
def _tracce() -> QuerySet[Traccia]:
    return Traccia.objects.select_related("elemento", "ordine", "reparto", "inserita_da")


# =========================================================================== tracce generiche
@require_GET
@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def tracce(request: HttpRequest) -> HttpResponse:
    return _lista(
        request,
        form_cls=forms.FiltriTracce,
        qs=_tracce(),
        template="tracciabilita/tracce.html",
        esporta=servizi.esporta_tracce,
        nome_file="ListaTracce.xlsx",
        predefiniti=_giorni_fa(config.leggi("tracciabilita.giorni_liste")),
    )


@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def traccia_nuova(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = forms.TracciaForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                traccia = servizi.registra_traccia(
                    utente=request.user, codice=d["codice"], chiave=d["chiave"], valore=d["valore"],
                    data=d["data"], reparto=d["reparto"], ordine=d["ordine"],
                )  # fmt: skip
            except OperazioneNonConsentita as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Traccia registrata per {traccia.elemento}.")
                return redirect(traccia)
    else:
        iniziali: dict[str, Any] = {k: request.GET.get(k, "")[:100] for k in ("codice", "ordine", "chiave", "valore")}
        form = forms.TracciaForm(initial={**iniziali, "data": timezone.localdate()})
    return render(request, "tracciabilita/traccia_nuova.html", {"form": form})


@require_GET
@richiede_permesso(F.TRACCIABILITA_CONSULTA, F.TRACCIABILITA_REGISTRA)
def traccia(request: HttpRequest, pk: int) -> HttpResponse:
    t = get_object_or_404(_tracce(), pk=pk)
    if is_cliente(request.user) and not t.ordine_id:
        raise Http404  # il cliente vede solo le tracce dei suoi ordini
    storia = _tracce().filter(elemento=t.elemento) if t.elemento_id else _tracce().filter(pk=t.pk)
    return render(request, "tracciabilita/traccia.html", {"traccia": t, "storia": storia[:100]})


@require_GET
@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def stampa(request: HttpRequest) -> HttpResponse:
    """Pagina da stampare con il codice a barre Code128 (generato nel browser con JsBarcode)."""
    testo = request.GET.get("barcode", "").strip()
    if not testo or len(testo) > 200 or not testo.isprintable() or not testo.isascii():
        return HttpResponseBadRequest("Parametro barcode mancante o non valido (testo ASCII, max 200 caratteri).")
    return render(request, "tracciabilita/stampa.html", {"barcode": testo})


# =========================================================================== pannello nella pagina dell'ordine
def _contesto_pannello(ordine: Ordine) -> dict[str, Any]:
    return {
        "ordine": ordine,
        "tracce": _tracce().filter(ordine=ordine)[:100],
        "controlli": ordine.controlli_qualita.select_related("test", "eseguito_da").prefetch_related(
            Prefetch("esiti", queryset=EsitoControllo.objects.select_related("controllo"))
        )[:50],
        "file": ordine.file_allegati.select_related("caricato_da")[:100],
        "dimensione_massima_mb": config.leggi("tracciabilita.dimensione_massima_file_mb"),
    }


@require_GET
@richiede_permesso(F.TRACCIABILITA_CONSULTA, F.TRACCIABILITA_REGISTRA)
def pannello_ordine(request: HttpRequest, ordine_id: int) -> HttpResponse:
    ordine = get_object_or_404(Ordine, pk=ordine_id)
    return render(request, "tracciabilita/_pannello_ordine.html", _contesto_pannello(ordine))


@require_POST
@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def lotto_busta(request: HttpRequest, ordine_id: int) -> HttpResponse:
    ordine = get_object_or_404(Ordine, pk=ordine_id)
    try:
        create = servizi.registra_lotto_busta(
            ordine.pk, request.POST.get("lotto", ""), request.POST.get("busta", ""), request.user
        )
        esito = "Tracciatura creata." if create else "Lotto e busta erano già registrati."
        ok = True
    except OperazioneNonConsentita as exc:
        esito, ok = str(exc), False
    if request.htmx:
        return render(
            request,
            "tracciabilita/_pannello_ordine.html",
            {**_contesto_pannello(ordine), "esito_lotto": esito, "ok": ok},
        )
    (messages.success if ok else messages.error)(request, esito)
    return redirect(ordine)


# =========================================================================== file per ordine
@require_POST
@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def file_ordine_carica(request: HttpRequest, ordine_id: int) -> HttpResponse:
    ordine = get_object_or_404(Ordine, pk=ordine_id)
    try:
        caricati = servizi.carica_file_ordine(ordine.pk, request.FILES.getlist("file"), request.user)
        messages.success(request, f"{len(caricati)} file caricati sull'ordine {ordine.codice_breve}.")
    except OperazioneNonConsentita as exc:
        messages.error(request, str(exc))
    return redirect(ordine)


@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def file_carica(request: HttpRequest) -> HttpResponse:
    """Caricamento di foto/file indicando il codice dell'ordine (pagina adatta al telefono)."""
    if request.method == "POST":
        form = forms.CaricaFileForm(request.POST)
        ordine = form.cleaned_data["ordine"] if form.is_valid() else None
        if ordine is None:
            messages.error(request, " ".join(form.errors.get("ordine", ["Scegli l'ordine dall'elenco."])))
        else:
            try:
                caricati = servizi.carica_file_ordine(ordine.pk, request.FILES.getlist("file"), request.user)
            except OperazioneNonConsentita as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"{len(caricati)} file caricati sull'ordine {ordine.codice_breve}.")
                return redirect(f"{reverse('tracciabilita:file_lista')}?{urlencode({'ordine': ordine.pk})}")
        # Redirect anche in caso di errore: il caricamento via fetch segue il redirect e mostra il messaggio.
        dopo = urlencode({"ordine": ordine.pk}) if ordine else ""
        return redirect(f"{reverse('tracciabilita:file_carica')}?{dopo}")
    form = forms.CaricaFileForm(initial={"ordine": request.GET.get("ordine", "")[:60] or None})
    return render(
        request,
        "tracciabilita/file_carica.html",
        {"form": form, "dimensione_massima_mb": config.leggi("tracciabilita.dimensione_massima_file_mb")},
    )


@require_GET
@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def file_lista(request: HttpRequest) -> HttpResponse:
    return _lista(
        request,
        form_cls=forms.FiltriFile,
        qs=FileOrdine.objects.select_related("ordine", "caricato_da"),
        template="tracciabilita/file_lista.html",
        esporta=servizi.esporta_file,
        nome_file="FileOrdini.xlsx",
        predefiniti=_giorni_fa(2),
    )


@require_GET
@richiede_permesso(F.TRACCIABILITA_CONSULTA, F.TRACCIABILITA_REGISTRA)
def file_scarica(request: HttpRequest, pk: int) -> HttpResponse:
    fo = get_object_or_404(FileOrdine, pk=pk)
    return _servi_file(fo.file, fo.nome_originale, fo.tipo_contenuto, request.GET.get("scarica") == "1")


def _servi_file(campo: Any, nome: str, tipo: str, allegato: bool) -> FileResponse:
    try:
        aperto = campo.open("rb")
    except (FileNotFoundError, OSError) as exc:
        raise Http404("File non disponibile.") from exc
    risposta = FileResponse(aperto, content_type=tipo, as_attachment=allegato, filename=nome)
    risposta["X-Content-Type-Options"] = "nosniff"
    risposta["Cache-Control"] = "private, max-age=3600"
    return risposta


@require_POST
@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def file_elimina(request: HttpRequest, pk: int) -> HttpResponse:
    try:
        ordine = servizi.elimina_file_ordine(pk, request.user)
    except OperazioneNonConsentita as exc:
        messages.error(request, str(exc))
        fo = FileOrdine.objects.filter(pk=pk).select_related("ordine").first()
        return redirect(fo.ordine if fo else "tracciabilita:file_lista")
    messages.success(request, "File eliminato.")
    return redirect(ordine)


# =========================================================================== controllo qualità
def _righe_controlli(test: Test | None, dati: Any) -> list[dict[str, Any]]:
    if test is None:
        return []
    righe = []
    for c in servizi.controlli_del_test(test):
        if c.tipo == TipoControllo.PRESENZA:
            presente = (dati.get(f"presente_{c.codice}") == "on") if dati else c.presente_predefinito
            righe.append({"controllo": c, "presente": presente})
        else:
            righe.append(
                {
                    "controllo": c,
                    "campione": dati.get(f"campione_{c.codice}", "") if dati else "",
                    "ko": dati.get(f"ko_{c.codice}", "") if dati else "",
                }
            )
    return righe


@require_GET
@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def qualita_controlli(request: HttpRequest) -> HttpResponse:
    """Parte del form con i controlli del test scelto (HTMX)."""
    test = Test.objects.filter(codice=request.GET.get("test", ""), attivo=True).first()
    return render(request, "tracciabilita/_controlli_test.html", {"righe": _righe_controlli(test, None)})


@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def qualita_nuovo(request: HttpRequest) -> HttpResponse:
    test = None
    if request.method == "POST":
        form = forms.ControlloQualitaForm(request.POST)
        test = Test.objects.filter(codice=request.POST.get("test", ""), attivo=True).first()
        if form.is_valid():
            d = form.cleaned_data
            try:
                cq = servizi.registra_controllo_qualita(
                    utente=request.user, test=d["test"], articolo=d["articolo"], data=d["data"],
                    risultati=request.POST, ordine=d["ordine"], riferimento=d["riferimento"],
                    qta_in_ordine=d["qta_in_ordine"],
                )  # fmt: skip
            except OperazioneNonConsentita as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Controllo qualità registrato: {cq}.")
                return redirect(f"{reverse('tracciabilita:qualita_nuovo')}?test={cq.test.codice}")
        righe = _righe_controlli(test, request.POST)
    else:
        test = Test.objects.filter(codice=request.GET.get("test", ""), attivo=True).first()
        form = forms.ControlloQualitaForm(
            initial={
                "ordine": request.GET.get("ordine", "")[:60],
                "articolo": request.GET.get("articolo", "")[:50],
                "data": timezone.localdate(),
                "test": test.codice if test else None,
            }
        )
        righe = _righe_controlli(test, None)
    return render(request, "tracciabilita/qualita_nuovo.html", {"form": form, "righe": righe})


@require_GET
@richiede_permesso(F.TRACCIABILITA_CONSULTA, F.TRACCIABILITA_REGISTRA)
def qualita(request: HttpRequest) -> HttpResponse:
    qs = EsitoControllo.objects.select_related(
        "controllo", "controllo_qualita__ordine", "controllo_qualita__test", "controllo_qualita__eseguito_da"
    ).order_by("-controllo_qualita__data", "-controllo_qualita_id", "controllo__tipo", "controllo__nome")
    return _lista(
        request,
        form_cls=forms.FiltriQualita,
        qs=qs,
        template="tracciabilita/qualita.html",
        esporta=servizi.esporta_qualita,
        nome_file="ControlliQualita.xlsx",
        predefiniti=_giorni_fa(30),
    )
