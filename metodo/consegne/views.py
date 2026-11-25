import datetime as dt

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Subquery, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from metodo.committenti import servizi as committenti
from metodo.committenti.models import Committente
from metodo.core.permessi import F, ha_permesso, richiede_permesso
from metodo.ordini.models import StatoOrdine
from metodo.ordini.servizi import OperazioneNonConsentita

from . import servizi
from .models import DDT, Consegna

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _download(nome: str, contenuto: bytes, tipo: str = "text/plain") -> HttpResponse:
    r = HttpResponse(bytes(contenuto), content_type=tipo)
    r["Content-Disposition"] = f'attachment; filename="{nome}"'
    return r


def _serie_scelte(request: HttpRequest) -> list[str]:
    return [s for s in request.GET.getlist("serie") if s.isdigit()][:10]


@require_GET
@richiede_permesso(F.CONSEGNA)
def pagina(request: HttpRequest) -> HttpResponse:
    serie = _serie_scelte(request)
    qs = committenti.filtra(servizi.ordini_da_consegnare(serie), request)
    # Le serie proposte sono quelle realmente presenti tra gli ordini da consegnare (in Metodo erano fisse).
    disponibili = sorted(
        {s[-2:] for s in committenti.filtra(servizi.ordini_da_consegnare(), request).values_list("serie", flat=True)}
    )
    ordini = []
    for o in qs:
        righe = servizi.righe_consegnabili(o)
        agg = righe.aggregate(n=Count("pk"), pezzi=Sum("qta"))
        ordini.append({"ordine": o, "n": agg["n"], "pezzi": agg["pezzi"] or 0})
    return render(
        request,
        "consegne/pagina.html",
        {
            "ordini": ordini,
            "serie": serie,
            "disponibili": disponibili,
            "totale_righe": sum(o["n"] for o in ordini),
            "puo_gestionale_ddt": ha_permesso(request.user, F.GESTIONALE_DDT),
        },
    )


@require_GET
@richiede_permesso(F.CONSEGNA)
def etichette(request: HttpRequest) -> HttpResponse:
    serie = _serie_scelte(request)
    qs = committenti.filtra(servizi.ordini_da_consegnare(serie), request)
    suffisso = "-".join(serie) or "Tutto"
    if request.GET.get("pulsanti"):
        qs = qs.filter(appendice__icontains="T")
        suffisso = f"T_{suffisso}"
    return _download(f"Etichette_{suffisso}.xlsx", servizi.etichette_xlsx(qs), XLSX)


@require_POST
@richiede_permesso(F.CONSEGNA)
def consegna(request: HttpRequest) -> HttpResponse:
    serie = [s for s in request.POST.getlist("serie") if s.isdigit()]
    ids = [int(i) for i in request.POST.getlist("ordine") if i.isdigit()]
    if not serie and not ids and request.POST.get("tutto") != "1":
        messages.error(request, "Selezione non valida.")
        return redirect("consegne:pagina")
    try:
        c = servizi.consegna(
            request.user,
            serie=serie,
            ordini_ids=ids,
            committenti=list(committenti.filtra(Committente.objects.all(), request, campo="pk")),
        )
    except OperazioneNonConsentita as exc:
        messages.error(request, str(exc))
        return redirect("consegne:pagina")
    messages.success(request, f"Consegna registrata: {c.righe.count()} righe. Scarica il file per il gestionale.")
    return redirect(c)


@require_POST
@richiede_permesso(F.CONSEGNA)
def rimuovi(request: HttpRequest, ordine_id: int) -> HttpResponse:
    from metodo.ordini import servizi as ordini

    try:
        ordini.cambia_stato(ordine_id, StatoOrdine.INSERITO, request.user)
    except OperazioneNonConsentita as exc:
        messages.error(request, str(exc))
    return redirect("consegne:pagina")


@richiede_permesso(F.CONSEGNA, F.DDT)
def storico(request: HttpRequest) -> HttpResponse:
    righe = Consegna.objects.filter(pk=OuterRef("pk")).annotate(n=Count("righe")).values("n")
    qs = Consegna.objects.select_related("creata_da").defer("file_contenuto").annotate(n_righe=Subquery(righe))
    return render(request, "consegne/storico.html", {"pagina": Paginator(qs, 30).get_page(request.GET.get("pagina"))})


@richiede_permesso(F.CONSEGNA, F.DDT)
def dettaglio(request: HttpRequest, pk: int) -> HttpResponse:
    c = get_object_or_404(Consegna.objects.defer("file_contenuto"), pk=pk)
    righe = c.righe.select_related("riga__ordine", "ddt").order_by(
        "riga__ordine__serie", "riga__ordine__numero", "riga__n_riga"
    )
    return render(request, "consegne/dettaglio.html", {"consegna": c, "righe": righe})


@richiede_permesso(F.CONSEGNA, F.DDT)
def file_consegna(request: HttpRequest, pk: int) -> HttpResponse:
    c = get_object_or_404(Consegna, pk=pk)
    return _download(c.file_nome, c.file_contenuto)


# ----------------------------------------------------------------------------------------- DDT
@richiede_permesso(F.DDT)
def ddt(request: HttpRequest) -> HttpResponse:
    """Righe consegnate in attesa di DDT: si crea il DDT da Metodo v2 (numerazione di legge, stampa e file
    tecnico) oppure si registra il numero di un DDT emesso dal gestionale esterno."""
    from metodo.gestionale import servizi as gestionale
    from metodo.gestionale.views import contesto_trasporto, emetti_da_richiesta

    oggi = dt.date.today()
    valori = gestionale.valori_predefiniti_ddt()
    if request.method == "POST" and request.POST.get("azione") == "crea":
        valori, d = emetti_da_richiesta(request)
        if d is not None:
            return redirect(f"{reverse('consegne:ddt_storico')}?creato={d.pk}")
    elif request.method == "POST":
        try:
            data = dt.date.fromisoformat(request.POST.get("data_manuale", ""))
            ids = [int(i) for i in request.POST.getlist("riga") if i.isdigit()]
            d = servizi.registra_ddt(request.POST.get("numero", ""), data, ids, request.user)
        except ValueError:
            messages.error(request, "Data del DDT non valida.")
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
        else:
            if d.email:
                messages.success(request, f"{d} registrato: file tecnico in invio.")
            else:
                messages.warning(
                    request,
                    f"{d} registrato, ma il file tecnico NON è stato inviato: "
                    "configura i destinatari in Configurazione.",
                )
            return redirect("consegne:ddt_storico")
    righe = servizi.righe_senza_ddt()
    return render(
        request,
        "consegne/ddt.html",
        {
            **contesto_trasporto(valori),
            "righe": righe,
            "n_righe": len(righe),
            "manuale": request.method == "POST" and request.POST.get("azione") != "crea",
            "data_manuale": request.POST.get("data_manuale", oggi.isoformat()),
            "numero": request.POST.get("numero", ""),
        },
    )


@richiede_permesso(F.DDT)
def ddt_storico(request: HttpRequest) -> HttpResponse:
    qs = (
        DDT.objects.select_related("creato_da", "email")
        .defer("file_contenuto")
        .annotate(n_righe=Count("righe"))
        .order_by("-anno", "-data", "-id")
    )
    creato = request.GET.get("creato", "")
    return render(
        request,
        "consegne/ddt_storico.html",
        {
            "pagina": Paginator(qs, 30).get_page(request.GET.get("pagina")),
            "creato": DDT.objects.filter(pk=int(creato)).first() if creato.isdigit() else None,
        },
    )


@richiede_permesso(F.DDT)
def ddt_file(request: HttpRequest, pk: int) -> HttpResponse:
    d = get_object_or_404(DDT, pk=pk)
    return _download(d.file_nome, d.file_contenuto)


@require_POST
@richiede_permesso(F.DDT_REINVIA)
def ddt_reinvia(request: HttpRequest, pk: int) -> HttpResponse:
    try:
        d = servizi.reinvia_file_tecnico(pk, request.user)
        messages.success(request, f"File tecnico del {d} rimesso in invio.")
    except OperazioneNonConsentita as exc:
        messages.error(request, str(exc))
    return redirect("consegne:ddt_storico")
