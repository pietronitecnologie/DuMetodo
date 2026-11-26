import datetime as dt
import io

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from metodo.committenti import servizi as committenti
from metodo.configurazione import servizi as config
from metodo.core.permessi import F, ha_permesso, richiede_permesso
from metodo.ordini.servizi import OperazioneNonConsentita
from metodo.pianificazione import calendario

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


def _ambito(request: HttpRequest) -> dict[str, object]:
    """Per l'avviso nei report: il committente scelto, se l'utente ne vede più d'uno."""
    return {"committente_filtrato": committenti.filtrato(request)}


@require_GET
@richiede_permesso(F.REPORT_DASHBOARD)
def dashboard(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "report/dashboard.html",
        {
            **servizi.dashboard(
                committenti=committenti.ids(request), parziale=committenti.filtrato(request) is not None
            ),
            **_ambito(request),
        },
    )


@require_GET
@richiede_permesso(F.REPORT_PRODUTTIVITA)
def produttivita(request: HttpRequest) -> HttpResponse:
    fino_a = _data(request.GET.get("fino_a")) or timezone.localdate()
    settimane = servizi.settimane_indietro(_numero_settimane(request), fino_a)
    ambito = _ambito(request)
    dati = servizi.produttivita(
        settimane, committenti.ids(request), parziale=ambito["committente_filtrato"] is not None
    )
    if request.GET.get("formato") == "xlsx":
        righe = [
            [
                s.lunedi,
                *[s.pezzi.get(c, 0) for c in servizi.CATEGORIE],
                s.pezzi_totali,
                s.valore,
                s.ore_teoriche,
                s.ore_timbrate,
                s.ore_manuali,
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
                "Ore timbrate",
                "Correzione a mano",
                "Ore lavorate (usate)",
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
            "dettaglio": (
                servizi.produttivita_giornaliera(calendario.lunedi(giorno), committenti.ids(request))
                if giorno
                else None
            ),
            "dettaglio_lunedi": calendario.lunedi(giorno) if giorno else None,
            **ambito,
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
        messages.success(request, "Correzioni salvate (dove sono vuote valgono le timbrature).")
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
    righe, settimane = servizi.materiale_per_reparto(
        servizi.settimane_indietro(_numero_settimane(request), fino_a), committenti.ids(request)
    )
    if request.GET.get("formato") == "xlsx":
        out = []
        for r in righe:
            for s, c in zip(settimane, r["celle"], strict=True):
                out.append([r["reparto"], s, c["scadenza"], c["consegnato"], c["timbrate"]])
        return _xlsx(
            "MaterialePerReparto.xlsx",
            ["Reparto", "Settimana", "Ore a scadenza", "Ore consegnate", "Ore timbrate"],
            out,
        )
    return render(
        request,
        "report/materiale.html",
        {"righe": righe, "settimane": settimane, "fino_a": fino_a, "n": len(settimane), **_ambito(request)},
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
        committenti=committenti.ids(request),
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
            **_ambito(request),
        },
    )


@require_GET
@richiede_permesso(F.REPORT_MATERIALE)
def settimane(request: HttpRequest) -> HttpResponse:
    """Dalla settimana corrente alle precedenti (quante: Configurazione → Produzione): ore in scadenza e consegnate."""
    fino_a = _data(request.GET.get("fino_a")) or timezone.localdate()
    n = _numero_settimane(request, default=min(int(config.leggi("produzione.report_settimane") or 9), 52))
    dati = servizi.evaso_consegnato_per_settimana(servizi.settimane_indietro(n, fino_a), committenti.ids(request))
    if request.GET.get("formato") == "xlsx":
        intestazione = ["Settimana dal", "Al"]
        for rep in dati["reparti"]:
            intestazione += [f"{rep} – in scadenza (h)", f"{rep} – consegnate (h)", f"{rep} – timbrate (h)"]
        intestazione += ["Totale in scadenza (h)", "Totale consegnate (h)", "Totale timbrate (h)"]
        out = []
        for r in dati["righe"]:
            riga = [r["lunedi"], r["domenica"]]
            for c in r["celle"]:
                riga += [c["scadenza"], c["consegnato"], c["timbrate"]]
            out.append([*riga, r["tot_scadenza"], r["tot_consegnato"], r["tot_timbrate"]])
        return _xlsx("EvasoConsegnatoPerSettimana.xlsx", intestazione, out)
    return render(request, "report/settimane.html", {**dati, "fino_a": fino_a, "n": n, **_ambito(request)})


# ------------------------------------------------------------------ controllo della produttività (settimanale)
def _periodo(request: HttpRequest) -> tuple[dt.date, list[dt.date]]:
    fino_a = _data(request.GET.get("fino_a")) or timezone.localdate()
    return fino_a, servizi.settimane_indietro(_numero_settimane(request), fino_a)


@require_GET
@richiede_permesso(F.REPORT_PRODUTTIVITA)
def efficienza_reparti(request: HttpRequest) -> HttpResponse:
    """Ore timbrate contro ore prodotte, per settimana e reparto."""
    fino_a, settimane = _periodo(request)
    dati = servizi.efficienza_reparti(settimane, con_costi=ha_permesso(request.user, F.TIMBRATURE_COSTI))
    if request.GET.get("formato") == "xlsx":
        intestazione = ["Settimana dal", "Al"]
        for rep in dati["reparti"]:
            intestazione += [f"{rep} – timbrate (h)", f"{rep} – prodotte (h)", f"{rep} – efficienza %"]
        intestazione += ["Totale timbrate (h)", "di cui fuori reparto (h)", "Totale prodotte (h)", "Efficienza %"]
        if dati["con_costi"]:
            intestazione += ["Costo manodopera (€)", "Costo per ora prodotta (€)"]
        out = []
        for r in dati["righe"]:
            riga = [r["lunedi"], r["domenica"]]
            for c in r["celle"]:
                riga += [c["timbrate"], c["prodotte"], c["efficienza"]]
            riga += [r["timbrate"], r["non_assegnate"], r["prodotte"], r["efficienza"]]
            if dati["con_costi"]:
                riga += [r["costo"], r["costo_ora_prodotta"]]
            out.append(riga)
        return _xlsx("EfficienzaPerReparto.xlsx", intestazione, out)
    # Le ore timbrate sono di tutta l'azienda: il confronto si fa sempre su tutti i clienti committenti.
    return render(
        request,
        "report/efficienza_reparti.html",
        {**dati, "fino_a": fino_a, "n": len(settimane), "tutta_azienda": _ambito(request)["committente_filtrato"]},
    )


@require_GET
@richiede_permesso(F.REPORT_PRODUTTIVITA)
def puntualita(request: HttpRequest) -> HttpResponse:
    """Consegne puntuali o in ritardo e arretrato a fine settimana."""
    fino_a, settimane = _periodo(request)
    righe = servizi.puntualita_arretrato(settimane, committenti.ids(request))
    if request.GET.get("formato") == "xlsx":
        return _xlsx(
            "PuntualitaArretrato.xlsx",
            [
                "Settimana dal",
                "Al",
                "Righe consegnate",
                "Puntuali",
                "In ritardo",
                "Puntualità %",
                "Ritardo medio (giorni)",
                "Arretrato a fine settimana (righe)",
                "Arretrato (h)",
            ],
            [
                [
                    r["lunedi"],
                    r["domenica"],
                    r["consegnate"],
                    r["puntuali"],
                    r["in_ritardo"],
                    r["puntualita"],
                    r["ritardo_medio"],
                    r["arretrato_righe"],
                    r["arretrato_ore"],
                ]
                for r in righe
            ],
        )
    return render(
        request,
        "report/puntualita.html",
        {
            "righe": righe,
            "fino_a": fino_a,
            "n": len(settimane),
            "resa_configurata": bool(config.leggi("produzione.prezzo_resa_oraria")),
            **_ambito(request),
        },
    )


@require_GET
@richiede_permesso(F.TIMBRATURE_VEDERE)
def ore_lavoratori(request: HttpRequest) -> HttpResponse:
    """Ore lavorate per lavoratore e settimana, con la quota passata in un reparto."""
    fino_a, settimane = _periodo(request)
    dati = servizi.ore_per_lavoratore(settimane)
    if request.GET.get("formato") == "xlsx":
        intestazione = ["Matricola", "Lavoratore"]
        for s in dati["settimane"]:
            intestazione += [f"{s:%d/%m/%Y} – ore", f"{s:%d/%m/%Y} – in reparto %"]
        intestazione += ["Totale ore", "Media settimanale", "In reparto %", "Anomalie"]
        out = []
        for r in dati["righe"]:
            riga = [r["lavoratore"].matricola, f"{r['lavoratore'].cognome} {r['lavoratore'].nome}"]
            for c in r["celle"]:
                riga += [c["ore"], c["in_reparto"]]
            out.append([*riga, r["totale"], r["media"], r["in_reparto"], r["anomalie"]])
        return _xlsx("OrePerLavoratore.xlsx", intestazione, out)
    return render(request, "report/ore_lavoratori.html", {**dati, "fino_a": fino_a, "n": len(settimane)})
