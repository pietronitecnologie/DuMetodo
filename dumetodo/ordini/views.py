from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Subquery, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from dumetodo.anagrafica.servizi import descrizione_subquery
from dumetodo.comunicazioni import servizi as note
from dumetodo.core.permessi import F, ha_permesso, richiede_permesso

from . import servizi, stati
from .aiuti_visivi import colora_cambi_articolo
from .forms import AttivaProduzioneForm, CambiaScadenzaForm, DividiRigaForm, FiltriOrdiniForm
from .models import Ordine, RigaOrdine, StatoRiga


@require_GET
@richiede_permesso(F.ORDINI_VEDERE)
def lista(request: HttpRequest) -> HttpResponse:
    dati = request.GET if request.GET else {"aperti": "on"}
    form = FiltriOrdiniForm(dati)
    qs = Ordine.objects.all()
    if form.is_valid():
        qs = form.filtra(qs)
    # Subquery (non JOIN) così i totali restano corretti anche con il filtro per articolo.
    righe = RigaOrdine.objects.filter(ordine=OuterRef("pk"), stato=StatoRiga.ATTIVA).order_by().values("ordine")
    qs = qs.annotate(
        n_righe=Subquery(righe.annotate(n=Count("pk")).values("n")),
        pezzi=Subquery(righe.annotate(t=Sum("qta")).values("t")),
    ).order_by("data_scadenza", "serie", "numero")
    qs = note.annota_non_lette(qs, request.user)
    pagina = Paginator(qs, 50).get_page(request.GET.get("pagina"))
    template = "ordini/_tabella.html" if request.htmx else "ordini/lista.html"
    return render(request, template, {"form": form, "pagina": pagina})


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


@richiede_permesso(F.ATTIVA_PRODUZIONE)
def attiva_produzione(request: HttpRequest) -> HttpResponse:
    form = AttivaProduzioneForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            n = servizi.attiva_produzione(form.cleaned_data["dal"], form.cleaned_data["al"], request.user)
            messages.success(request, f"{n} ordini attivati (stato 'Lavorato con mancanze').")
            return redirect("ordini:lista")
        except servizi.OperazioneNonConsentita as exc:
            form.add_error(None, str(exc))
    return render(request, "ordini/attiva_produzione.html", {"form": form})
