from __future__ import annotations

import io

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from metodo.committenti import servizi as committenti
from metodo.core.permessi import F, ha_permesso, richiede_permesso
from metodo.ordini.models import Ordine

from . import servizi
from .forms import CommentoForm, FiltriTicketForm, NuovoTicketForm
from .models import Priorita, Stato, Ticket


def _base():  # type: ignore[no-untyped-def]
    return Ticket.objects.select_related("ordine", "riga", "aperto_da", "assegnato_a", "chiuso_da")


def _visibili(request: HttpRequest, qs=None):  # type: ignore[no-untyped-def]
    """I ticket del committente scelto (o di quelli dell'utente); quelli senza ordine li vedono tutti."""
    qs = qs if qs is not None else _base()
    c = committenti.corrente(request)
    visibili = [c.pk] if c is not None else committenti.accessibili(request.user)
    return qs.filter(Q(ordine__isnull=True) | Q(ordine__committente__in=visibili))


@require_GET
@richiede_permesso(F.TICKET_VEDERE, F.TICKET_GESTIRE)
def lista(request: HttpRequest) -> HttpResponse:
    form = FiltriTicketForm(request.GET or None)
    qs, solo_ritardo = form.filtra(_visibili(request), request.user)
    # Non chiusi: prima i più urgenti e i più vecchi; con gli altri filtri, i più recenti.
    aperti = (request.GET.get("stato") or "aperti") in ("aperti", "ritardo") or not request.GET
    elenco = servizi.con_ore(qs.order_by("-priorita", "aperto_il") if aperti else qs.order_by("-aperto_il"))
    if solo_ritardo:
        elenco = [t for t in elenco if t.ore.in_ritardo]
    if request.GET.get("formato") == "xlsx":
        return _excel(elenco)
    contesto = {
        "form": form,
        "pagina": Paginator(elenco, 50).get_page(request.GET.get("pagina")),
        "stat": servizi.statistiche(_visibili(request, Ticket.objects.all())),
    }
    template = "ticket/_tabella.html" if request.htmx else "ticket/lista.html"
    return render(request, template, contesto)


@require_http_methods(["GET", "POST"])
@richiede_permesso(F.TICKET_APRIRE, F.TICKET_GESTIRE)
def nuovo(request: HttpRequest) -> HttpResponse:
    ordine = None
    if request.method == "GET" and (pk := request.GET.get("ordine", "")).isdigit():
        ordine = committenti.ordine_o_404(request, int(pk))
    form = NuovoTicketForm(
        request.POST or None, ordine=ordine, ordini=committenti.filtra(Ordine.objects.all(), request)
    )
    if request.GET.get("riga", "").isdigit() and not form.is_bound:
        form.fields["riga"].initial = int(request.GET["riga"])
    if request.method == "POST" and form.is_valid():
        try:
            t = servizi.apri(request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error("riga", exc)
        else:
            messages.success(request, f"Ticket #{t.pk} aperto.")
            return redirect(t)
    return render(request, "ticket/nuovo.html", {"form": form, "ordine": ordine})


@require_GET
@richiede_permesso(F.TICKET_VEDERE, F.TICKET_GESTIRE)
def dettaglio(request: HttpRequest, pk: int) -> HttpResponse:
    t = get_object_or_404(_visibili(request), pk=pk)
    t.ore = servizi.ore(t)  # type: ignore[attr-defined]
    utente = request.user
    return render(
        request,
        "ticket/dettaglio.html",
        {
            "t": t,
            "eventi": t.eventi.select_related("autore"),
            "commento": CommentoForm(),
            "puo_commentare": ha_permesso(utente, F.TICKET_APRIRE, F.TICKET_GESTIRE),
            "puo_lavorare": servizi.puo_lavorare(utente, t),
            "puo_prendere": servizi.puo_prendere(utente, t),
            "puo_riaprire": t.is_chiuso and (ha_permesso(utente, F.TICKET_GESTIRE) or utente.pk == t.aperto_da_id),
            "puo_gestire": ha_permesso(utente, F.TICKET_GESTIRE),
            "incaricabili": servizi.incaricabili() if ha_permesso(utente, F.TICKET_GESTIRE) else [],
            "priorita": Priorita.choices,
            "Stato": Stato,
        },
    )


@require_POST
@richiede_permesso(F.TICKET_APRIRE, F.TICKET_GESTIRE)
def azione(request: HttpRequest, pk: int) -> HttpResponse:
    t = get_object_or_404(_visibili(request, Ticket.objects.all()), pk=pk)
    cosa = request.POST.get("azione", "")
    try:
        match cosa:
            case "commenta":
                servizi.commenta(t, request.user, request.POST.get("testo", ""))
            case "stato":
                servizi.cambia_stato(t, request.user, request.POST.get("stato", ""), request.POST.get("nota", ""))
            case "assegna":
                pk_utente = request.POST.get("utente", "")
                a = get_object_or_404(servizi.incaricabili(), pk=int(pk_utente)) if pk_utente.isdigit() else None
                servizi.assegna(t, request.user, a)
            case "priorita":
                servizi.cambia_priorita(t, request.user, request.POST.get("priorita", ""))
            case _:
                messages.error(request, "Azione sconosciuta.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect(t)


@require_GET
@richiede_permesso(F.TICKET_VEDERE, F.TICKET_GESTIRE)
def pannello_ordine(request: HttpRequest, pk: int) -> HttpResponse:
    """I ticket dell'ordine, nella sua pagina (caricati a parte con HTMX)."""
    ordine = committenti.ordine_o_404(request, pk)
    elenco = servizi.con_ore(_base().filter(ordine=ordine).order_by("-aperto_il"))
    elenco.sort(key=lambda t: t.is_chiuso)  # prima quelli ancora aperti
    return render(request, "ticket/_pannello_ordine.html", {"ordine": ordine, "elenco": elenco})


def _excel(elenco: list[Ticket]) -> HttpResponse:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Ticket"
    ws.append(
        [
            "N°",
            "Titolo",
            "Ordine",
            "Riga",
            "Categoria",
            "Priorità",
            "Stato",
            "Aperto da",
            "Aperto il",
            "Assegnato a",
            "Preso in carico il",
            "Chiuso il",
            "Ore totali",
            "Ore in attesa",
            "Ore effettive",
            "Ore di risposta",
            "Ore massime",
            "In ritardo",
            "Soluzione",
        ]
    )
    for c in ws[1]:
        c.font = Font(bold=True)

    def senza_fuso(d):  # type: ignore[no-untyped-def]
        from django.utils import timezone

        return timezone.localtime(d).replace(tzinfo=None) if d else None

    for t in elenco:
        o = t.ore  # type: ignore[attr-defined]
        ws.append(
            [
                t.pk,
                t.titolo,
                t.ordine.codice if t.ordine else "",
                t.riga.n_riga if t.riga else "",
                t.get_categoria_display(),
                t.get_priorita_display(),
                t.get_stato_display(),
                servizi.nome(t.aperto_da),
                senza_fuso(t.aperto_il),
                servizi.nome(t.assegnato_a),
                senza_fuso(t.preso_in_carico_il),
                senza_fuso(t.chiuso_il),
                float(o.totali),
                float(o.attesa),
                float(o.effettive),
                float(o.risposta) if o.risposta is not None else None,
                o.soglia,
                "sì" if o.in_ritardo else "",
                t.soluzione,
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    r = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    r["Content-Disposition"] = 'attachment; filename="Ticket.xlsx"'
    return r
