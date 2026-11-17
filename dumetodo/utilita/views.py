import datetime as dt

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from dumetodo.consegne.servizi import etichette_xlsx
from dumetodo.core.permessi import F, ha_permesso, is_fornitore, richiede_permesso
from dumetodo.ordini.models import Ordine
from dumetodo.ordini.servizi import OperazioneNonConsentita

from . import servizi
from .models import Annuncio

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FUNZIONI_UTILITA = (
    F.UTILITA_ERRORI_IMPORTAZIONE,
    F.UTILITA_RISTAMPA_ETICHETTE,
    F.UTILITA_ANNUNCI,
    F.UTILITA_SCORPORAZIONE,
    F.UTILITA_CAMBIA_ORDINE,
)


def _data(v: str | None, default: dt.date | None = None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(v) if v else default
    except ValueError:
        return default


@require_GET
@richiede_permesso(*FUNZIONI_UTILITA)
def indice(request: HttpRequest) -> HttpResponse:
    return render(request, "utilita/indice.html")


# ----------------------------------------------------------------------------------------- annunci
@richiede_permesso(F.UTILITA_ANNUNCI)
def annunci(request: HttpRequest) -> HttpResponse:
    return render(request, "utilita/annunci.html", {"annunci": Annuncio.objects.select_related("creato_da")[:100]})


@richiede_permesso(F.UTILITA_ANNUNCI)
def annuncio(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    a = get_object_or_404(Annuncio, pk=pk) if pk else None
    if request.method == "POST":
        try:
            servizi.salva_annuncio(
                request.user,
                a,
                data=_data(request.POST.get("data"), timezone.localdate()),
                testo=request.POST.get("testo", ""),
                attivo=request.POST.get("attivo") == "on",
                permanente=request.POST.get("permanente") == "on",
            )
            messages.success(request, "Annuncio salvato.")
            return redirect("utilita:annunci")
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
    return render(request, "utilita/annuncio.html", {"annuncio": a, "oggi": timezone.localdate()})


@require_POST
@richiede_permesso(F.UTILITA_ANNUNCI)
def annuncio_attiva(request: HttpRequest, pk: int) -> HttpResponse:
    a = servizi.attiva_disattiva(request.user, pk)
    messages.success(request, f"Annuncio {'attivato' if a.attivo else 'disattivato'}.")
    return redirect("utilita:annunci")


@require_GET
def annunci_attivi(request: HttpRequest) -> HttpResponse:
    """Frammento HTMX con gli annunci visibili: solo per gli utenti del Fornitore (come in Metodo)."""
    if not is_fornitore(request.user):
        return HttpResponse("")
    return render(request, "utilita/_annunci_attivi.html", {"annunci": Annuncio.objects.visibili()})


# ----------------------------------------------------------------------------------------- ristampa etichette
@richiede_permesso(F.UTILITA_RISTAMPA_ETICHETTE)
def ristampa_etichette(request: HttpRequest) -> HttpResponse:
    dati = request.POST if request.method == "POST" else request.GET
    numero = dati.get("numero", "").strip()
    anno = int(dati["anno"]) if dati.get("anno", "").isdigit() else timezone.localdate().year
    ordini = servizi.ordini_per_ddt(numero, anno) if numero else Ordine.objects.none()
    if request.method == "POST":
        scelti = ordini.filter(pk__in=servizi.ids_da_post(request.POST.getlist("ordine")))
        if scelti.exists():
            r = HttpResponse(etichette_xlsx(scelti), content_type=XLSX)
            r["Content-Disposition"] = f'attachment; filename="Etichette_DDT_{numero}_{anno}.xlsx"'
            return r
        messages.error(request, "Seleziona almeno un ordine.")
    altri_anni = [a for a in servizi.anni_del_ddt(numero) if a != anno] if numero and not ordini else []
    return render(
        request,
        "utilita/ristampa_etichette.html",
        {"numero": numero, "anno": anno, "ordini": ordini, "altri_anni": altri_anni},
    )


# ----------------------------------------------------------------------------------------- scorporazione
@richiede_permesso(F.UTILITA_SCORPORAZIONE)
def scorporazione(request: HttpRequest) -> HttpResponse:
    dati = request.POST if request.method == "POST" else request.GET
    oggi = timezone.localdate()
    dal = _data(dati.get("dal"), oggi - dt.timedelta(days=30))
    al = _data(dati.get("al"), oggi)
    articolo, variante = dati.get("articolo", ""), dati.get("variante", "")
    if request.method == "POST":
        try:
            n = servizi.scorpora_massa(
                request.user,
                servizi.ids_da_post(request.POST.getlist("visualizzata")),
                servizi.ids_da_post(request.POST.getlist("scorpora")),
            )
            messages.success(request, f"Aggiornate {n} righe." if n else "Nessuna modifica da salvare.")
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
    righe = servizi.righe_consegnate(articolo, variante, dal, al) if articolo.strip() else None
    return render(
        request,
        "utilita/scorporazione.html",
        {
            "righe": righe[:500] if righe is not None else None,
            "troppe": righe is not None and righe.count() > 500,
            "articolo": articolo,
            "variante": variante,
            "dal": dal,
            "al": al,
            "periodi": {
                "Settimana corrente": (oggi - dt.timedelta(days=oggi.weekday()), oggi),
                "Settimana scorsa": (
                    oggi - dt.timedelta(days=oggi.weekday() + 7),
                    oggi - dt.timedelta(days=oggi.weekday() + 1),
                ),
                "Mese corrente": (oggi.replace(day=1), oggi),
                "Mese scorso": (
                    (oggi.replace(day=1) - dt.timedelta(days=1)).replace(day=1),
                    oggi.replace(day=1) - dt.timedelta(days=1),
                ),
            },
        },
    )


# ----------------------------------------------------------------------------------------- cambia ordine
@richiede_permesso(F.UTILITA_CAMBIA_ORDINE)
def cambia_ordine_cerca(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    ordini = Ordine.objects.filter(numero__icontains=q).order_by("-anno", "numero")[:50] if q else []
    return render(request, "utilita/cambia_ordine_cerca.html", {"q": q, "ordini": ordini})


@richiede_permesso(F.UTILITA_CAMBIA_ORDINE)
def cambia_ordine(request: HttpRequest, pk: int) -> HttpResponse:
    ordine = get_object_or_404(Ordine, pk=pk)
    if request.method == "POST":
        try:
            ordine = servizi.cambia_ordine(
                request.user,
                pk,
                numero=request.POST.get("numero", ""),
                serie=request.POST.get("serie", ""),
                appendice=request.POST.get("appendice", ""),
                data_scadenza=_data(request.POST.get("data_scadenza")),
                motivo=request.POST.get("motivo", ""),
            )
            messages.success(request, f"Ordine aggiornato: ora è {ordine.codice}.")
            return redirect(ordine)
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
    return render(
        request,
        "utilita/cambia_ordine.html",
        {
            "ordine": ordine,
            "correlati": servizi.ordini_correlati(ordine),
            "storico": ordine.storico_scadenze.select_related("utente")[:20],
            "vede_prezzi": ha_permesso(request.user, F.ORDINI_PREZZI),
        },
    )
