import datetime as dt

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from dumetodo.anagrafica.models import Tabulato
from dumetodo.core.models import Reparto
from dumetodo.core.permessi import F, richiede_permesso
from dumetodo.ordini.models import Ordine
from dumetodo.ordini.servizi import OperazioneNonConsentita

from . import calendario, piano, servizi


def _data(valore: str | None, default: dt.date) -> dt.date:
    try:
        return dt.date.fromisoformat(valore) if valore else default
    except ValueError:
        return default


def _xlsx(nome: str, contenuto: bytes) -> HttpResponse:
    r = HttpResponse(contenuto, content_type=servizi.XLSX)
    r["Content-Disposition"] = f'attachment; filename="{nome}"'
    return r


@require_GET
@richiede_permesso(F.PIANIFICAZIONE)
def pagina(request: HttpRequest) -> HttpResponse:
    p = piano.calcola()
    return render(
        request,
        "pianificazione/piano.html",
        {
            "piano": p,
            "reparti": Reparto.objects.filter(attivo=True),
            "colonne_settimane": list(zip(p.etichette, range(len(p.settimane)), strict=True)),
        },
    )


@require_POST
@richiede_permesso(F.PIANIFICAZIONE_CAPACITA)
def soglie(request: HttpRequest) -> HttpResponse:
    errori = 0
    for reparto in Reparto.objects.filter(attivo=True):
        chiave = f"soglia_{reparto.pk}"
        if chiave not in request.POST:
            continue
        try:
            servizi.aggiorna_soglia(reparto.pk, request.POST[chiave], request.user)
        except OperazioneNonConsentita as exc:
            errori += 1
            messages.error(request, f"{reparto}: {exc}")
    if not errori:
        messages.success(request, "Capacità dei reparti aggiornate.")
    return redirect("pianificazione:pagina")


@require_GET
@richiede_permesso(F.PIANIFICAZIONE)
def export(request: HttpRequest) -> HttpResponse:
    nome, contenuto, _ = servizi.genera_xlsx()
    return _xlsx(nome, contenuto)


@require_POST
@richiede_permesso(F.PIANIFICAZIONE)
def invia_report(request: HttpRequest) -> HttpResponse:
    try:
        e = servizi.invia_report(
            request.user,
            destinatari=request.POST.get("destinatari"),
            oggetto=request.POST.get("oggetto", ""),
            messaggio=request.POST.get("messaggio", ""),
        )
        messages.success(request, f"Report in invio a {', '.join(e.destinatari)}.")
    except OperazioneNonConsentita as exc:
        messages.error(request, str(exc))
    return redirect("pianificazione:pagina")


def _filtra_agenda(request: HttpRequest):  # type: ignore[no-untyped-def]
    qs = Ordine.objects.all()
    for campo, lookup in (
        ("q", "numero__icontains"),
        ("serie", "serie__icontains"),
        ("appendice", "appendice__icontains"),
        ("anno", "anno"),
    ):
        valore = request.GET.get(campo, "").strip()
        if valore and (campo != "anno" or valore.isdigit()):
            qs = qs.filter(**{lookup: valore})
    return qs


@require_GET
@richiede_permesso(F.AGENDA)
def agenda(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "pianificazione/agenda.html",
        {
            "colonne": servizi.agenda(_filtra_agenda(request)),
            "titolo": "Agenda ordini",
            "pulsanti": False,
        },
    )


@require_GET
@richiede_permesso(F.AGENDA)
def agenda_pulsanti(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "pianificazione/agenda.html",
        {
            "colonne": servizi.agenda_pulsanti(_filtra_agenda(request)),
            "titolo": "Agenda pulsanti",
            "pulsanti": True,
        },
    )


def _tabulato(request: HttpRequest, tipo: str) -> HttpResponse:
    reparti = Reparto.objects.filter(attivo=True)
    reparto = (
        get_object_or_404(reparti, pk=request.GET["reparto"]) if request.GET.get("reparto", "").isdigit() else None
    )
    lunedi = calendario.lunedi(_data(request.GET.get("settimana"), timezone.localdate()))
    voci, totale = (
        ([], 0)
        if reparto is None
        else (
            servizi.tabulato_settimanale(reparto, lunedi)
            if tipo == "settimanale"
            else servizi.tabulato_scorte(reparto, lunedi)
        )
    )
    contesto = {
        "tipo": tipo,
        "reparti": reparti,
        "reparto": reparto,
        "lunedi": lunedi,
        "domenica": lunedi + dt.timedelta(days=6),
        "voci": voci,
        "totale": totale,
        "precedente": lunedi - dt.timedelta(weeks=1),
        "successiva": lunedi + dt.timedelta(weeks=1),
    }
    if reparto and request.GET.get("formato") == "xlsx":
        import io

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Reparto", "Articolo", "Variante", "Ordine", "Quantità", "Tempo (ore)"])
        for v in voci:
            ws.append(
                [
                    reparto.nome,
                    v.articolo,
                    v.variante,
                    v.ordine.codice_breve if v.ordine else "",
                    float(v.qta),
                    float(v.ore) if v.ore is not None else None,
                ]
            )
        buf = io.BytesIO()
        wb.save(buf)
        return _xlsx(f"Tabulato_{tipo}_{reparto.codice}_{lunedi:%Y%m%d}.xlsx", buf.getvalue())
    template = (
        "pianificazione/tabulato_stampa.html"
        if request.GET.get("formato") == "stampa"
        else "pianificazione/tabulato.html"
    )
    return render(request, template, contesto)


@require_GET
@richiede_permesso(F.PIANIFICAZIONE)
def tabulato_settimanale(request: HttpRequest) -> HttpResponse:
    return _tabulato(request, "settimanale")


@require_GET
@richiede_permesso(F.PIANIFICAZIONE)
def tabulato_scorte(request: HttpRequest) -> HttpResponse:
    return _tabulato(request, "scorte")


@require_GET
@richiede_permesso(F.PIANIFICAZIONE)
def tabulato_commesse(request: HttpRequest) -> HttpResponse:
    oggi = timezone.localdate()
    lun, dom = calendario.settimana(oggi)
    dal, al = _data(request.GET.get("dal"), lun), _data(request.GET.get("al"), dom)
    n = int(request.GET["tabulato"]) if request.GET.get("tabulato", "").isdigit() else None
    ordini = (
        servizi.commesse_per_tabulato(n, dal, al) if n is not None and n in Tabulato.values else Ordine.objects.none()
    )
    if n is not None and request.GET.get("formato") == "xlsx":
        import io

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Commessa", "Articolo", "Variante", "Quantità", "Stato", "Data scadenza"])
        for o in ordini.prefetch_related("righe"):
            for r in o.righe.all():
                if r.is_attiva:
                    ws.append([o.codice, r.articolo, r.variante, float(r.qta), o.get_stato_display(), o.data_scadenza])
                    ws.cell(ws.max_row, 6).number_format = "DD/MM/YYYY"
        buf = io.BytesIO()
        wb.save(buf)
        return _xlsx(f"CommesseTabulato_{n}_{dal:%Y%m%d}_{al:%Y%m%d}.xlsx", buf.getvalue())
    return render(
        request,
        "pianificazione/tabulato_commesse.html",
        {
            "tabulati": [t for t in Tabulato if t.value],
            "tabulato": n,
            "dal": dal,
            "al": al,
            "ordini": ordini,
        },
    )
