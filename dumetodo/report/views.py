import datetime as dt
import io

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from dumetodo.core.permessi import F, richiede_permesso
from dumetodo.ordini.servizi import OperazioneNonConsentita
from dumetodo.pianificazione import calendario

from . import servizi

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _data(v: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(v) if v else None
    except ValueError:
        return None


def _numero_settimane(request: HttpRequest, default: int = 8) -> int:
    v = request.GET.get("settimane", "")
    return max(1, min(int(v), 52)) if v.isdigit() else default


def _xlsx(nome: str, intestazione: list[str], righe: list[list[object]]) -> HttpResponse:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.append(intestazione)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in righe:
        ws.append([float(v) if hasattr(v, "is_finite") else v for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type=XLSX)
    resp["Content-Disposition"] = f'attachment; filename="{nome}"'
    return resp


@require_GET
@richiede_permesso(F.REPORT_DASHBOARD)
def dashboard(request: HttpRequest) -> HttpResponse:
    return render(request, "report/dashboard.html", servizi.dashboard())


@require_GET
@richiede_permesso(F.REPORT_PRODUTTIVITA)
def produttivita(request: HttpRequest) -> HttpResponse:
    fino_a = _data(request.GET.get("fino_a")) or timezone.localdate()
    settimane = servizi.settimane_indietro(_numero_settimane(request), fino_a)
    dati = servizi.produttivita(settimane)
    if request.GET.get("formato") == "xlsx":
        righe = [
            [
                s.lunedi,
                *[s.pezzi.get(c, 0) for c in servizi.CATEGORIE],
                s.pezzi_totali,
                s.valore,
                s.ore_teoriche,
                s.ore_lavorate,
                s.pezzi_ora,
                s.efficienza,
            ]
            for s in dati
        ]
        return _xlsx(
            f"Produttivita_{settimane[0]:%Y%m%d}_{settimane[-1]:%Y%m%d}.xlsx",
            [
                "Settimana",
                *servizi.CATEGORIE,
                "Pezzi totali",
                "Valore consegnato",
                "Ore teoriche",
                "Ore lavorate",
                "Pezzi/ora",
                "Efficienza %",
            ],
            righe,
        )
    giorno = _data(request.GET.get("dettaglio"))
    return render(
        request,
        "report/produttivita.html",
        {
            "settimane": dati,
            "categorie": servizi.CATEGORIE,
            "fino_a": fino_a,
            "n": len(settimane),
            "dettaglio": servizi.produttivita_giornaliera(calendario.lunedi(giorno)) if giorno else None,
            "dettaglio_lunedi": calendario.lunedi(giorno) if giorno else None,
        },
    )


@require_POST
@richiede_permesso(F.REPORT_PRODUTTIVITA)
def ore_lavorate(request: HttpRequest) -> HttpResponse:
    errori = 0
    for chiave, valore in request.POST.items():
        if not chiave.startswith("ore_"):
            continue
        lunedi = _data(chiave.removeprefix("ore_"))
        if lunedi is None:
            continue
        try:
            servizi.salva_ore_lavorate(lunedi, valore, request.user)
        except OperazioneNonConsentita as exc:
            errori += 1
            messages.error(request, f"{lunedi:%d/%m/%Y}: {exc}")
    if not errori:
        messages.success(request, "Ore lavorate salvate.")
    from django.urls import reverse
    from django.utils.http import urlencode

    parametri = {}
    if request.POST.get("settimane", "").isdigit():
        parametri["settimane"] = request.POST["settimane"]
    if _data(request.POST.get("fino_a")):
        parametri["fino_a"] = request.POST["fino_a"]
    return redirect(f"{reverse('report:produttivita')}?{urlencode(parametri)}")


@require_GET
@richiede_permesso(F.REPORT_MATERIALE)
def materiale(request: HttpRequest) -> HttpResponse:
    fino_a = _data(request.GET.get("fino_a")) or timezone.localdate()
    righe, settimane = servizi.materiale_per_reparto(servizi.settimane_indietro(_numero_settimane(request), fino_a))
    if request.GET.get("formato") == "xlsx":
        out = []
        for r in righe:
            for s, c in zip(settimane, r["celle"], strict=True):
                out.append([r["reparto"], s, c["scadenza"], c["consegnato"]])
        return _xlsx("MaterialePerReparto.xlsx", ["Reparto", "Settimana", "Ore a scadenza", "Ore consegnate"], out)
    return render(
        request,
        "report/materiale.html",
        {"righe": righe, "settimane": settimane, "fino_a": fino_a, "n": len(settimane)},
    )


@require_GET
@richiede_permesso(F.REPORT_PREZZI)
def prezzi(request: HttpRequest) -> HttpResponse:
    oggi = timezone.localdate()
    origine = "lavorazione" if request.GET.get("origine") == "lavorazione" else "bolle"
    default_dal = None if origine == "lavorazione" else oggi - dt.timedelta(days=14)
    filtri = servizi.FiltriPrezzi(
        origine=origine,
        dal=_data(request.GET.get("dal")) if "dal" in request.GET else default_dal,
        al=_data(request.GET.get("al")) if "al" in request.GET else (None if origine == "lavorazione" else oggi),
        articolo=request.GET.get("articolo", ""),
        numero_ddt=request.GET.get("ddt", ""),
        solo_differenze=request.GET.get("tutte") != "1",
        senza_riferimento=request.GET.get("senza_rif") == "1",
    )
    qs = servizi.controllo_prezzi(filtri)
    if request.GET.get("formato") == "xlsx":
        righe = [
            [
                r.ordine.codice,
                r.n_riga,
                r.articolo,
                r.variante,
                r.qta,
                r.prezzo_unitario,
                r.prezzo_rif,
                r.differenza,
                r.differenza_totale,
                f"{r.ddt_numero}/{r.ddt_anno}" if r.ddt_numero else "",
                r.data_consegna if origine == "bolle" else r.data_scadenza,
            ]
            for r in qs
        ]
        return _xlsx(
            f"ControlloPrezzi_{origine}.xlsx",
            [
                "Ordine",
                "Riga",
                "Articolo",
                "Variante",
                "Qtà",
                "Prezzo riga",
                "Prezzo anagrafica",
                "Differenza unitaria",
                "Differenza totale",
                "DDT",
                "Data consegna" if origine == "bolle" else "Scadenza",
            ],
            righe,
        )
    return render(
        request,
        "report/prezzi.html",
        {
            "filtri": filtri,
            "pagina": Paginator(qs, 100).get_page(request.GET.get("pagina")),
            "totale": servizi.totale_differenze(qs),
        },
    )
