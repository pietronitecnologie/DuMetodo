from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Subquery, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from metodo.anagrafica.servizi import descrizione_subquery
from metodo.comunicazioni import servizi as note
from metodo.core.permessi import F, ha_permesso, richiede_permesso

from . import selezione, servizi, stati
from .aiuti_visivi import colora_cambi_articolo
from .forms import CambiaScadenzaForm, DividiRigaForm, FiltriOrdiniForm
from .models import Ordine, RigaOrdine, StatoRiga


@require_GET
@richiede_permesso(F.ORDINI_VEDERE)
def lista(request: HttpRequest) -> HttpResponse:
    # Gli ordini sono migliaia: senza almeno un filtro la pagina resta vuota (niente elenco enorme
    # caricato a ogni apertura).
    form = FiltriOrdiniForm(request.GET)
    qs = Ordine.objects.none()
    if form.is_valid() and form.ha_filtri():
        qs = form.filtra(Ordine.objects.all())
    # Subquery (non JOIN) così i totali restano corretti anche con il filtro per articolo.
    righe = RigaOrdine.objects.filter(ordine=OuterRef("pk"), stato=StatoRiga.ATTIVA).order_by().values("ordine")
    qs = qs.annotate(
        n_righe=Subquery(righe.annotate(n=Count("pk")).values("n")),
        pezzi=Subquery(righe.annotate(t=Sum("qta")).values("t")),
    ).order_by("data_scadenza", "serie", "numero")
    qs = note.annota_non_lette(qs, request.user)
    pagina = Paginator(qs, 50).get_page(request.GET.get("pagina"))
    template = "ordini/_tabella.html" if request.htmx else "ordini/lista.html"
    filtri_attivi = form.is_valid() and form.ha_filtri()
    return render(request, template, {"form": form, "pagina": pagina, "filtri_attivi": filtri_attivi})


@require_GET
@richiede_permesso(F.ORDINI_VEDERE)
def dettaglio(request: HttpRequest, pk: int) -> HttpResponse:
    ordine = get_object_or_404(Ordine.objects.select_related("reparto"), pk=pk)
    mostra_tutte = request.GET.get("tutte") == "1" and ha_permesso(request.user, F.ORDINI_RIGHE)
    righe = ordine.righe.all() if mostra_tutte else ordine.righe.attive()
    note.segna_lette(ordine, request.user)
    transizioni = [t for t in stati.transizioni_possibili(ordine.stato) if ha_permesso(request.user, t.permesso)]
    return render(
        request,
        "ordini/dettaglio.html",
        {
            "ordine": ordine,
            "righe": colora_cambi_articolo(
                righe.select_related("riga_origine").annotate(descrizione=descrizione_subquery())
            ),
            "mostra_tutte": mostra_tutte,
            "transizioni": transizioni,
            "storico": ordine.storico_scadenze.select_related("riga", "utente")[:50],
            "ore_teoriche": servizi.ore_teoriche(ordine),
            "totale_pezzi": servizi.totale_pezzi(ordine),
            "note": note.note_visibili(ordine, request.user),
            "form_scadenza": CambiaScadenzaForm(initial={"nuova": ordine.data_scadenza}),
        },
    )


@require_POST
@richiede_permesso(F.ORDINI_STATO, F.ORDINI_ANNULLA)
def cambia_stato(request: HttpRequest, pk: int) -> HttpResponse:
    try:
        ordine = servizi.cambia_stato(pk, request.POST.get("stato", ""), request.user)
        messages.success(request, f"Ordine {ordine.codice_breve}: stato '{ordine.get_stato_display()}'.")
    except servizi.OperazioneNonConsentita as exc:
        messages.error(request, str(exc))
    return redirect("ordini:dettaglio", pk=pk)


@require_POST
@richiede_permesso(F.ORDINI_RIGHE)
def azione_riga(request: HttpRequest, pk: int, riga_id: int) -> HttpResponse:
    get_object_or_404(RigaOrdine, pk=riga_id, ordine_id=pk)
    try:
        riga = servizi.azione_riga(riga_id, request.POST.get("azione", ""), request.user)
        messages.success(request, f"Riga {riga.n_riga} aggiornata.")
    except servizi.OperazioneNonConsentita as exc:
        messages.error(request, str(exc))
    return redirect("ordini:dettaglio", pk=pk)


@richiede_permesso(F.ORDINI_DIVIDI)
def dividi_riga(request: HttpRequest, pk: int, riga_id: int) -> HttpResponse:
    riga = get_object_or_404(RigaOrdine.objects.select_related("ordine"), pk=riga_id, ordine_id=pk)
    form = DividiRigaForm(request.POST or None, initial={"nuova_scadenza": riga.data_scadenza})
    if request.method == "POST" and form.is_valid():
        try:
            nuova = servizi.dividi_riga(
                riga.pk, form.cleaned_data["qta"], request.user, form.cleaned_data["nuova_scadenza"]
            )
            messages.success(request, f"Creata la riga {nuova.n_riga} con {nuova.qta} pezzi.")
            return redirect("ordini:dettaglio", pk=pk)
        except servizi.OperazioneNonConsentita as exc:
            form.add_error(None, str(exc))
    return render(request, "ordini/dividi_riga.html", {"riga": riga, "form": form})


@require_POST
@richiede_permesso(F.ORDINI_DIVIDI)
def cambia_scadenza(request: HttpRequest, pk: int) -> HttpResponse:
    form = CambiaScadenzaForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Indica una data valida e il motivo.")
    else:
        try:
            servizi.cambia_scadenza_ordine(pk, form.cleaned_data["nuova"], form.cleaned_data["motivo"], request.user)
            messages.success(request, "Scadenza aggiornata.")
        except servizi.OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
    return redirect("ordini:dettaglio", pk=pk)


@require_GET
@richiede_permesso(F.ORDINI_VEDERE, F.UTILITA_CAMBIA_ORDINE, F.CONSEGNA, F.TRACCIATURA_REGISTRA)
def seleziona(request: HttpRequest) -> HttpResponse:
    """Elenco degli ordini per il campo di selezione (HTMX)."""
    testo = request.GET.get(f"{request.GET.get('campo', 'ordine')}_cerca", "")[:60].strip()
    return render(request, "ordini/_selezione.html", {"testo": testo, "ordini": list(selezione.cerca(testo))})


@require_GET
@richiede_permesso(F.ORDINI_VEDERE)
def vai(request: HttpRequest) -> HttpResponse:
    """Ricerca rapida della barra in alto: apre l'ordine scelto."""
    campo = selezione.CampoOrdine(required=False)
    try:
        ordine = campo.clean(campo.widget.value_from_datadict(request.GET, None, "ordine"))
    except forms.ValidationError:
        ordine = None
    if ordine is None:
        messages.error(request, "Ordine non trovato: sceglilo dall'elenco.")
        indietro = request.headers.get("referer", "")
        sicuro = url_has_allowed_host_and_scheme(indietro, {request.get_host()}, request.is_secure())
        return redirect(indietro if sicuro else "ordini:lista")
    return redirect(ordine)
