from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Subquery, Sum
from django.http import HttpRequest, HttpResponse, JsonResponse
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
    segnati = marcati(request)
    return render(
        request,
        template,
        {
            "form": form,
            "pagina": pagina,
            "filtri_attivi": filtri_attivi,
            "marcati_altrove": _marcati_altrove(qs, pagina, segnati) if filtri_attivi else [],
        },
    )


# Gli ordini «segnalibro» (lista ordini e agenda): dove l'operatore è arrivato a controllare. Restano finché è
# collegato (sono nella sessione: l'uscita li cancella); un clic sullo stesso segnalibro lo toglie.
CHIAVE_MARCATI = "ordini_marcati"


def marcati(request: HttpRequest) -> list[int]:
    return list(request.session.get(CHIAVE_MARCATI) or [])


def _marcati_altrove(qs, pagina, segnati: list[int]) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    """Gli ordini segnati che sono nella lista ma non in questa pagina: [{ordine, pagina}] per andarci."""
    in_pagina = {o.pk for o in pagina}
    altrove = [pk for pk in segnati if pk not in in_pagina]
    if not altrove:
        return []
    posizione = {pk: i for i, pk in enumerate(qs.values_list("pk", flat=True))}
    ordini = Ordine.objects.in_bulk([pk for pk in altrove if pk in posizione])
    return [
        {"ordine": ordini[pk], "pagina": posizione[pk] // pagina.paginator.per_page + 1}
        for pk in sorted(ordini, key=posizione.__getitem__)
    ]


@require_POST
@richiede_permesso(F.ORDINI_VEDERE, F.AGENDA)
def marca(request: HttpRequest, pk: int) -> HttpResponse:
    """Mette il segnalibro sull'ordine, o lo toglie se c'è già; gli altri segnalibri restano."""
    get_object_or_404(Ordine, pk=pk)
    segnati = marcati(request)
    if pk in segnati:
        segnati.remove(pk)
    else:
        segnati.append(pk)
    request.session[CHIAVE_MARCATI] = segnati[-200:]
    return JsonResponse({"marcato": pk in segnati})


@require_GET
@richiede_permesso(F.ORDINI_VEDERE)
def articoli(request: HttpRequest) -> HttpResponse:
    """Ricerca articoli: gli stessi filtri dell'elenco ordini, ma una riga per articolo d'ordine (articolo, ordine,
    numero di riga, quantità). Con il filtro Articolo restano solo le righe di quell'articolo."""
    form = FiltriOrdiniForm(request.GET)
    filtri_attivi = form.is_valid() and form.ha_filtri()
    righe = RigaOrdine.objects.none()
    if filtri_attivi:
        ordini = form.filtra(Ordine.objects.all())
        righe = RigaOrdine.objects.filter(stato=StatoRiga.ATTIVA, ordine__in=ordini.values("pk"))
        if form.cleaned_data.get("articolo"):
            righe = righe.filter(articolo__icontains=form.cleaned_data["articolo"].strip())
    righe = (
        righe.select_related("ordine")
        .annotate(descrizione=descrizione_subquery())
        .order_by("articolo", "variante", "data_scadenza", "ordine__numero", "n_riga")
    )
    if request.GET.get("formato") == "xlsx":
        return _excel_articoli(righe[:50000])
    totali = righe.aggregate(pezzi=Sum("qta"), articoli=Count("articolo", distinct=True))
    template = "ordini/_tabella_articoli.html" if request.htmx else "ordini/articoli.html"
    return render(
        request,
        template,
        {
            "form": form,
            "filtri_attivi": filtri_attivi,
            "pagina": Paginator(righe, 100).get_page(request.GET.get("pagina")),
            "totale_pezzi": totali["pezzi"] or 0,
            "n_articoli": totali["articoli"],
        },
    )


def _excel_articoli(righe) -> HttpResponse:  # type: ignore[no-untyped-def]
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Articoli"
    ws.append(["Articolo", "Variante", "Descrizione", "Ordine", "Riga", "Quantità", "Scadenza", "Stato ordine"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in righe:
        ws.append(
            [
                r.articolo,
                r.variante,
                r.descrizione or "",
                r.ordine.codice,
                r.n_riga,
                float(r.qta),
                r.data_scadenza,
                r.ordine.get_stato_display(),
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    risposta = HttpResponse(
        buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    risposta["Content-Disposition"] = 'attachment; filename="RicercaArticoli.xlsx"'
    return risposta


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
