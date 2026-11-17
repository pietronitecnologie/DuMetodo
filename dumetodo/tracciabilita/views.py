"""Viste di tracciabilità e qualità: sottili, chiamano ``servizi``. Ogni modifica è POST con CSRF,
tranne l'API delle postazioni, autenticata con token (niente cookie, quindi niente CSRF)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Prefetch, QuerySet
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from dumetodo.configurazione import servizi as config
from dumetodo.core.permessi import F, is_cliente, richiede_permesso
from dumetodo.ordini.models import Ordine

from . import forms, servizi
from .models import (
    Chiave,
    EsitoControllo,
    FileOrdine,
    Postazione,
    StatoWorkOrder,
    Test,
    TipoControllo,
    TipoElemento,
    Traccia,
    WorkOrderBatteria,
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


# =========================================================================== batterie
def _tracce() -> QuerySet[Traccia]:
    return Traccia.objects.select_related("elemento", "ordine", "reparto", "postazione", "inserita_da")


@require_GET
@richiede_permesso(F.TRACCIABILITA_CONSULTA, F.TRACCIABILITA_REGISTRA)
def batterie(request: HttpRequest) -> HttpResponse:
    return _lista(
        request,
        form_cls=forms.FiltriTracce,
        qs=_tracce().filter(elemento__tipo=TipoElemento.BATTERIA),
        template="tracciabilita/batterie.html",
        esporta=servizi.esporta_batterie,
        nome_file="ListaBatterie.xlsx",
    )


@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def batteria_nuova(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = forms.BatteriaForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                traccia = servizi.registra_batteria(
                    utente=request.user, azione=d["azione"], data=d["data"], reparto=d["reparto"],
                    codice=d["codice"], ordine=d["ordine"],
                )  # fmt: skip
            except OperazioneNonConsentita as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Batteria {traccia.elemento}: {traccia.chiave_leggibile} registrato.")
                return redirect(traccia)
    else:
        codice = request.GET.get("codice", "")[:50]
        form = forms.BatteriaForm(
            initial={
                "codice": codice,
                "azione": Chiave.RICARICA if codice else Chiave.INGRESSO,
                "data": timezone.localdate(),
            }
        )
    return render(request, "tracciabilita/batteria_nuova.html", {"form": form})


@require_GET
@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def genera_codice(request: HttpRequest) -> HttpResponse:
    """Propone un codice batteria libero (HTMX: sostituisce il campo codice). Sola lettura."""
    try:
        codice = servizi.genera_codice(servizi.prefisso_batterie())
    except OperazioneNonConsentita as exc:
        return HttpResponse(str(exc), status=409)
    return render(request, "tracciabilita/_campo_codice.html", {"codice": codice})


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
    visibile_al_cliente = t.ordine_id or (t.elemento and t.elemento.tipo != TipoElemento.GENERICO)
    if is_cliente(request.user) and not visibile_al_cliente:
        raise Http404  # il cliente vede solo ciò che vede nelle liste batterie/cavi o nell'ordine
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


# =========================================================================== cavi
@require_GET
@richiede_permesso(F.TRACCIABILITA_CONSULTA, F.TRACCIABILITA_REGISTRA)
def cavi(request: HttpRequest) -> HttpResponse:
    return _lista(
        request,
        form_cls=forms.FiltriTracce,
        qs=_tracce()
        .filter(elemento__tipo=TipoElemento.CAVO)
        .exclude(chiave=Chiave.INTESTATO)
        .annotate(intestato=Exists(Traccia.objects.filter(elemento=OuterRef("elemento"), chiave=Chiave.INTESTATO))),
        template="tracciabilita/cavi.html",
        esporta=servizi.esporta_cavi,
        nome_file="ListaCavi.xlsx",
        predefiniti=_giorni_fa(config.leggi("tracciabilita.giorni_liste")),
    )


@require_POST
@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def cavo_intestato(request: HttpRequest, elemento_id: int) -> HttpResponse:
    try:
        t = servizi.registra_intestatura(elemento_id, request.user)
        esito, classe = f"Intestato {timezone.localtime(t.data):%d/%m/%Y %H:%M}", "success"
    except OperazioneNonConsentita as exc:
        esito, classe = str(exc), "danger"
    if request.htmx:
        return render(request, "tracciabilita/_esito.html", {"esito": esito, "classe": classe})
    messages.info(request, esito)
    return redirect("tracciabilita:cavi")


@csrf_exempt
def api_cavi(request: HttpRequest) -> HttpResponse:
    """API dei banchi (ex ``insertTracciaCavi``, che in Metodo era una GET anonima). Protocollo nel README."""
    if request.method not in ("POST", "GET"):
        return JsonResponse({"errore": "Metodo non ammesso: usa POST."}, status=405)
    if request.method == "GET" and not config.leggi("tracciabilita.api_consenti_get"):
        return JsonResponse({"errore": "Metodo non ammesso: usa POST."}, status=405)
    token = request.headers.get("X-Postazione-Token") or request.GET.get("token") or request.POST.get("token")
    postazione = servizi.postazione_da_token(token)
    if postazione is None:
        return JsonResponse({"errore": "Token della postazione mancante o non valido."}, status=401)
    form = forms.CavoPostazioneForm(request.POST if request.method == "POST" else request.GET, postazione=postazione)
    if not form.is_valid():
        stato = 404 if form.has_error("ordine", code="non_trovato") else 400
        errori = {campo: [str(e) for e in lista] for campo, lista in form.errors.items()}
        primo = next(iter(errori.values()))[0]
        return JsonResponse({"errore": primo, "campi": errori}, status=stato)
    d = form.cleaned_data
    try:
        t, creata = servizi.registra_cavo_da_postazione(
            postazione, ordine=d["ordine"], prodotto=d["prodotto"], misura=d["misura"], lotto=d["lotto"],
            data=d["data"], reparto=d["reparto"],
        )  # fmt: skip
    except OperazioneNonConsentita as exc:
        return JsonResponse({"errore": str(exc)}, status=409)
    return JsonResponse(
        {
            "esito": "creata" if creata else "duplicata",
            "id": t.pk,
            "codice": t.elemento.codice if t.elemento else "",
            "ordine": t.ordine.codice if t.ordine else "",
        },
        status=201 if creata else 200,
    )


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
        testo = request.POST.get("ordine", "")[:60]
        try:
            ordine = forms.CampoOrdine(required=True).clean(testo)
            caricati = servizi.carica_file_ordine(ordine.pk, request.FILES.getlist("file"), request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{len(caricati)} file caricati sull'ordine {ordine.codice_breve}.")
            return redirect(
                f"{reverse('tracciabilita:file_lista')}?{urlencode({'ordine': ordine.numero, 'anno': ordine.anno})}"
            )
        # Redirect anche in caso di errore: il caricamento via fetch segue il redirect e mostra il messaggio.
        return redirect(f"{reverse('tracciabilita:file_carica')}?{urlencode({'ordine': testo})}")
    return render(
        request,
        "tracciabilita/file_carica.html",
        {
            "testo": request.GET.get("ordine", "")[:60],
            "formato": servizi.FORMATO_ORDINE,
            "dimensione_massima_mb": config.leggi("tracciabilita.dimensione_massima_file_mb"),
        },
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


# =========================================================================== work order batterie
@require_GET
@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def work_order(request: HttpRequest) -> HttpResponse:
    stato = request.GET.get("stato", StatoWorkOrder.APERTO)
    qs = WorkOrderBatteria.objects.select_related("elemento", "ultima_traccia__reparto", "chiuso_da")
    if stato in StatoWorkOrder.values:
        qs = qs.filter(stato=stato)
    pagina = Paginator(qs, PER_PAGINA).get_page(request.GET.get("pagina"))
    return render(
        request,
        "tracciabilita/work_order.html",
        {"pagina": pagina, "stato": stato, "giorni": config.leggi("tracciabilita.giorni_ricarica_batterie")},
    )


@require_POST
@richiede_permesso(F.TRACCIABILITA_REGISTRA)
def work_order_chiudi(request: HttpRequest, pk: int) -> HttpResponse:
    try:
        wo = servizi.chiudi_work_order(pk, request.user)
        messages.success(request, f"Work order della batteria {wo.elemento} chiuso.")
    except OperazioneNonConsentita as exc:
        messages.error(request, str(exc))
    return redirect("tracciabilita:work_order")


@require_POST
@richiede_permesso(F.TRACCIABILITA_WORK_ORDER)
def work_order_genera(request: HttpRequest) -> HttpResponse:
    n = servizi.genera_work_order_batterie()
    messages.success(request, f"Work order aperti: {n}.")
    return redirect("tracciabilita:work_order")


# =========================================================================== postazioni (token)
@require_GET
@richiede_permesso(F.TRACCIABILITA_POSTAZIONI)
def postazioni(request: HttpRequest) -> HttpResponse:
    return render(
        request, "tracciabilita/postazioni.html", {"postazioni": Postazione.objects.select_related("reparto")}
    )


@require_POST
@richiede_permesso(F.TRACCIABILITA_POSTAZIONI)
def postazione_token(request: HttpRequest, pk: int) -> HttpResponse:
    postazione = get_object_or_404(Postazione, pk=pk)
    token = servizi.genera_token_postazione(postazione.pk, request.user)
    # Il token viene mostrato solo in questa risposta (non in un messaggio salvato nella sessione).
    risposta = render(
        request,
        "tracciabilita/postazioni.html",
        {
            "postazioni": Postazione.objects.select_related("reparto"),
            "nuovo_token": token,
            "postazione_token": postazione,
            "url_api": request.build_absolute_uri(reverse("tracciabilita:api_cavi")),
        },
    )
    risposta["Cache-Control"] = "no-store"
    return risposta


@require_POST
@richiede_permesso(F.TRACCIABILITA_POSTAZIONI)
def postazione_revoca(request: HttpRequest, pk: int) -> HttpResponse:
    p = servizi.revoca_token_postazione(get_object_or_404(Postazione, pk=pk).pk, request.user)
    messages.success(request, f"Token della postazione {p.codice} revocato.")
    return redirect("tracciabilita:postazioni")


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
