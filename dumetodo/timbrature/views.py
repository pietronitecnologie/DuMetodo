import datetime as dt

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from dumetodo.core.models import Reparto
from dumetodo.core.permessi import F, ha_permesso, richiede_permesso
from dumetodo.ordini.servizi import OperazioneNonConsentita

from . import servizi
from .models import Identificativo, Lavoratore, Postazione, Timbratura, TipoIdentificativo, TipoTimbratura


def _data(v: str | None, predefinita: dt.date) -> dt.date:
    try:
        return dt.date.fromisoformat(v) if v else predefinita
    except ValueError:
        return predefinita


# ------------------------------------------------------------------------------- postazione (senza login)
def _minuscola(testo: str) -> str:
    """«Inizio lavoro – Pulsanti» → «inizio lavoro – Pulsanti» (solo la prima lettera)."""
    return testo[:1].lower() + testo[1:]


def _postazione(token: str) -> Postazione:
    p = servizi.postazione_da_token(token)
    if p is None:
        raise Http404
    return p


@require_GET
def postazione(request: HttpRequest, token: str) -> HttpResponse:
    """Pagina della postazione: va aperta dal client desktop (modalità chiosco) del PC della postazione."""
    p = _postazione(token)
    from django.conf import settings

    r = render(
        request,
        "timbrature/postazione.html",
        {"p": p, "fuso": settings.TIME_ZONE, "adesso_ms": int(timezone.now().timestamp() * 1000)},
    )
    r["Cache-Control"] = "no-store"
    r["X-Robots-Tag"] = "noindex"
    return r


@require_POST
def postazione_identifica(request: HttpRequest, token: str) -> JsonResponse:
    p = _postazione(token)
    try:
        return JsonResponse({"ok": True, **servizi.identifica(p, request.POST.get("codice", ""))})
    except OperazioneNonConsentita as exc:
        return JsonResponse({"ok": False, "errore": str(exc)}, status=400)


@require_POST
def postazione_timbra(request: HttpRequest, token: str) -> JsonResponse:
    p = _postazione(token)
    try:
        t = servizi.timbra(p, request.POST.get("codice", ""), request.POST.get("tipo", ""))
    except OperazioneNonConsentita as exc:
        return JsonResponse({"ok": False, "errore": str(exc)}, status=400)
    ora = timezone.localtime(t.momento)
    saluto = "Buongiorno" if ora.hour < 13 else "Buonasera" if ora.hour >= 18 else "Buon pomeriggio"
    if t.tipo == TipoTimbratura.USCITA:
        saluto = "Arrivederci"
    return JsonResponse(
        {
            "ok": True,
            "messaggio": f"{saluto} {t.lavoratore.nome}: {_minuscola(servizi._etichetta(t.tipo, p))} alle {ora:%H:%M}.",
        }
    )


# ------------------------------------------------------------------------------------------- presenze
@require_GET
@richiede_permesso(F.TIMBRATURE_VEDERE)
def presenze(request: HttpRequest) -> HttpResponse:
    oggi = timezone.localdate()
    dal = _data(request.GET.get("dal"), oggi)
    al = max(_data(request.GET.get("al"), dal), dal)
    if (al - dal).days > 92:
        al = dal + dt.timedelta(days=92)
    righe, reparti = servizi.presenze(dal, al)
    if request.GET.get("formato") == "xlsx":
        from dumetodo.report.views import _xlsx

        out = [
            [
                r.lavoratore.matricola,
                str(r.lavoratore),
                r.lavorato,
                r.mensa_pausa,
                r.non_assegnato,
                *[r.per_reparto.get(rep.pk) for rep in reparti],
                "; ".join(r.anomalie),
            ]
            for r in righe
        ]
        return _xlsx(
            f"Presenze_{dal}_{al}.xlsx",
            [
                "Matricola",
                "Lavoratore",
                "Ore lavorate",
                "Mensa e pause",
                "Non assegnate",
                *[rep.nome for rep in reparti],
                "Anomalie",
            ],
            out,
        )
    return render(
        request,
        "timbrature/presenze.html",
        {
            "righe": righe,
            "reparti": reparti,
            "dal": dal,
            "al": al,
            "un_giorno": dal == al,
            "oggi": dal <= oggi <= al,
            "presenti": sum(1 for r in righe if r.in_corso),
        },
    )


# ----------------------------------------------------------------------------------------- timbrature
@richiede_permesso(F.TIMBRATURE_VEDERE)
def timbrature(request: HttpRequest) -> HttpResponse:
    oggi = timezone.localdate()
    if request.method == "POST":
        p = request.POST
        try:
            if p.get("azione") == "annulla":
                servizi.annulla(request.user, int(p["timbratura"]), p.get("motivo", ""))
                messages.success(request, "Timbratura annullata.")
            else:
                momento = timezone.make_aware(dt.datetime.fromisoformat(p.get("momento", "")))
                servizi.timbratura_manuale(
                    request.user,
                    int(p["lavoratore"]),
                    p.get("tipo", ""),
                    momento,
                    int(p["reparto"]) if p.get("reparto", "").isdigit() else None,
                    p.get("nota", ""),
                )
                messages.success(request, "Timbratura aggiunta.")
        except (KeyError, ValueError):
            messages.error(request, "Valori non validi.")
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
        return redirect(request.get_full_path())
    dal = _data(request.GET.get("dal"), oggi)
    al = max(_data(request.GET.get("al"), dal), dal)
    qs = Timbratura.objects.select_related("lavoratore", "postazione", "reparto", "creata_da").filter(
        momento__date__gte=dal, momento__date__lte=al
    )
    lav = request.GET.get("lavoratore", "")
    if lav.isdigit():
        qs = qs.filter(lavoratore_id=int(lav))
    return render(
        request,
        "timbrature/timbrature.html",
        {
            "timbrature": qs.order_by("-momento")[:1000],
            "dal": dal,
            "al": al,
            "lavoratore": lav,
            "lavoratori": Lavoratore.objects.filter(attivo=True),
            "reparti": Reparto.objects.filter(attivo=True).order_by("nome"),
            "tipi": TipoTimbratura.choices,
            "puo_gestire": ha_permesso(request.user, F.TIMBRATURE_GESTIRE),
            "adesso": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        },
    )


# ----------------------------------------------------------------------------------------- lavoratori
@require_GET
@richiede_permesso(F.TIMBRATURE_VEDERE)
def lavoratori(request: HttpRequest) -> HttpResponse:
    qs = Lavoratore.objects.select_related("reparto")
    if request.GET.get("tutti") != "1":
        qs = qs.filter(attivo=True)
    return render(
        request,
        "timbrature/lavoratori.html",
        {
            "lavoratori": qs,
            "tutti": request.GET.get("tutti") == "1",
            "puo_gestire": ha_permesso(request.user, F.TIMBRATURE_GESTIRE),
            "puo_costi": ha_permesso(request.user, F.TIMBRATURE_COSTI),
        },
    )


@richiede_permesso(F.TIMBRATURE_GESTIRE)
def lavoratore(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    lav = get_object_or_404(Lavoratore, pk=pk) if pk else None
    codice_mostrato = ""
    if request.method == "POST":
        p = request.POST
        azione = p.get("azione", "dati")
        try:
            if azione == "dati":
                lav = servizi.salva_lavoratore(
                    request.user, lav.pk if lav else None, {**p.dict(), "attivo": "attivo" in p}
                )
                messages.success(request, f"{lav} salvato.")
                if not pk:
                    codice_mostrato = servizi.genera_codice(request.user, lav.pk)
            elif azione == "genera" and lav:
                codice_mostrato = servizi.genera_codice(request.user, lav.pk)
            elif azione == "imposta" and lav:
                servizi.imposta_codice(request.user, lav.pk, p.get("codice", ""))
                messages.success(request, "Codice impostato.")
            elif azione == "associa" and lav:
                i = servizi.aggiungi_identificativo(
                    request.user, lav.pk, p.get("tipo", ""), p.get("valore", ""), p.get("descrizione", "")
                )
                messages.success(request, f"{i.get_tipo_display()} associato a {lav}.")
            elif azione == "genera_badge" and lav:
                i = servizi.genera_badge(request.user, lav.pk)
                messages.success(request, f"Badge {i.valore} creato: stampalo.")
            elif azione in ("disattiva", "riattiva") and lav:
                i = get_object_or_404(lav.identificativi, pk=int(p.get("identificativo", "0")))
                servizi.disattiva_identificativo(request.user, i.pk, attivo=azione == "riattiva")
                messages.success(request, "Badge o tessera aggiornati.")
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
        if not codice_mostrato and lav:
            return redirect(lav)
    r = render(
        request,
        "timbrature/lavoratore.html",
        {
            "lav": lav,
            "reparti": Reparto.objects.filter(attivo=True).order_by("nome"),
            "codice_mostrato": codice_mostrato,
            "puo_costi": ha_permesso(request.user, F.TIMBRATURE_COSTI),
            "identificativi": lav.identificativi.all() if lav else [],
            "tipi_identificativo": TipoIdentificativo.choices,
        },
    )
    r["Cache-Control"] = "no-store"  # il codice appena generato non resta nella cache del browser
    return r


# ----------------------------------------------------------------------------------------- postazioni
@richiede_permesso(F.TIMBRATURE_GESTIRE)
def postazioni(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        p = request.POST
        try:
            if p.get("azione") == "rigenera":
                servizi.rigenera_link(request.user, int(p["postazione"]))
                messages.success(request, "Nuovo link creato: aggiornalo nel client della postazione.")
            else:
                pk = int(p["postazione"]) if p.get("postazione", "").isdigit() else None
                pos = servizi.salva_postazione(
                    request.user,
                    pk,
                    p.get("nome", ""),
                    p.get("reparto"),
                    "attiva" in p or not pk,
                    {k: k in p for k in ("tastierino", "lettori", "fotocamera")},
                )
                messages.success(request, f"Postazione «{pos}» salvata.")
        except (KeyError, ValueError):
            messages.error(request, "Valori non validi.")
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
        return redirect("timbrature:postazioni")
    adesso = timezone.now()
    lista = []
    for pos in Postazione.objects.select_related("reparto"):
        lista.append(
            {
                "p": pos,
                "link": request.build_absolute_uri(reverse("timbrature:postazione", args=[pos.token])),
                "collegata": pos.ultimo_contatto is not None
                and adesso - pos.ultimo_contatto < dt.timedelta(minutes=15),
            }
        )
    return render(
        request,
        "timbrature/postazioni.html",
        {"postazioni": lista, "reparti": Reparto.objects.filter(attivo=True).order_by("nome")},
    )


# --------------------------------------------------------------------------------------------- costi
@require_GET
@richiede_permesso(F.TIMBRATURE_COSTI)
def costi(request: HttpRequest) -> HttpResponse:
    oggi = timezone.localdate()
    inizio_mese = oggi.replace(day=1)
    dal = _data(request.GET.get("dal"), inizio_mese)
    al = max(_data(request.GET.get("al"), oggi), dal)
    if (al - dal).days > 370:
        al = dal + dt.timedelta(days=370)
    dati = servizi.costi_per_reparto(dal, al)
    if request.GET.get("formato") == "xlsx":
        from dumetodo.report.views import _xlsx

        return _xlsx(
            f"CostiPerReparto_{dal}_{al}.xlsx",
            [
                "Reparto",
                "Ore lavorate",
                "Costo (€)",
                "Ore teoriche consegnate",
                "Efficienza %",
                "Costo per ora teorica (€)",
            ],
            [
                [r["reparto"], r["ore"], r["costo"], r["teoriche"], r["efficienza"], r["costo_ora_teorica"]]
                for r in dati["righe"]
            ],
        )
    return render(request, "timbrature/costi.html", {**dati, "dal": dal, "al": al})


@require_GET
@richiede_permesso(F.TIMBRATURE_GESTIRE)
def badge(request: HttpRequest, pk: int) -> HttpResponse:
    """Badge stampabile: nome, matricola, codice a barre (Code 128) e QR con lo stesso valore."""
    i = get_object_or_404(Identificativo.objects.select_related("lavoratore"), pk=pk, tipo=TipoIdentificativo.BADGE)
    r = render(request, "timbrature/badge.html", {"i": i, "lav": i.lavoratore})
    r["Cache-Control"] = "no-store"
    return r
