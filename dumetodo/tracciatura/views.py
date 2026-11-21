from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from dumetodo.core.permessi import F, ha_permesso, richiede_permesso
from dumetodo.ordini.models import Ordine
from dumetodo.ordini.selezione import CampoOrdine
from dumetodo.ordini.servizi import OperazioneNonConsentita

from . import servizi
from .models import Campo, Categoria, Collegamento, Esito, Modello, TipoCampo, Tracciatura, Uso, Valore


class OrdineForm(forms.Form):
    ordine = CampoOrdine(required=False)


def _permessi(request: HttpRequest) -> dict[str, bool]:
    return {
        "puo_registrare": ha_permesso(request.user, F.TRACCIATURA_REGISTRA),
        "puo_configurare": ha_permesso(request.user, F.TRACCIATURA_CONFIGURA),
    }


# ---------------------------------------------------------------------------------------------------- elenco
@require_GET
@richiede_permesso(F.TRACCIATURA_VEDERE)
def elenco(request: HttpRequest) -> HttpResponse:
    filtri = request.GET.dict()
    qs = servizi.cerca(filtri)
    modello = (
        Modello.objects.filter(pk=filtri.get("modello") or None).first()
        if filtri.get("modello", "").isdigit()
        else None
    )
    if request.GET.get("formato") == "xlsx":
        return _excel(qs[:20000], modello)
    return render(
        request,
        "tracciatura/elenco.html",
        {
            **_permessi(request),
            "pagina": Paginator(qs, 50).get_page(request.GET.get("pagina")),
            "f": filtri,
            "modelli": Modello.objects.all(),
            "categorie": Categoria.choices,
            "query": request.GET.urlencode(),
        },
    )


def _excel(qs, modello: Modello | None) -> HttpResponse:
    from dumetodo.report.views import _xlsx

    campi = list(modello.campi.order_by("ordine", "id")) if modello else []
    intestazione = [
        "Data e ora",
        "Modello",
        "Esito",
        "Ordine",
        "DDT",
        "Codice articolo",
        "Codice finito",
        "Lotto",
        "Riferimento",
        "Operatore",
        "Note",
        *[c.etichetta + (f" ({c.unita})" if c.unita else "") for c in campi],
    ]
    righe = []
    for t in qs.prefetch_related("valori__campo"):
        valori = {v.campo_id: v for v in t.valori.all()}
        da_campo = []
        for c in campi:
            v = valori.get(c.pk)
            if v is None:
                da_campo.append(None)
            elif c.tipo == TipoCampo.INTERO:
                da_campo.append(v.intero)
            elif c.tipo == TipoCampo.DECIMALE:
                da_campo.append(float(v.decimale) if v.decimale is not None else None)
            else:
                da_campo.append(v.testo_leggibile)
        righe.append(
            [
                t.momento.replace(tzinfo=None),
                t.modello.nome,
                t.get_esito_display(),
                t.ordine.codice if t.ordine else "",
                f"{t.ddt.numero}/{t.ddt.anno}" if t.ddt else "",
                t.componente,
                t.articolo,
                t.lotto,
                t.riferimento,
                str(t.operatore),
                t.note,
                *da_campo,
            ]
        )
    return _xlsx("Tracciature.xlsx", intestazione, righe)


# ---------------------------------------------------------------------------------------------- registrazione
def _contesto_collegamenti(request: HttpRequest) -> dict[str, str]:
    src = request.POST if request.method == "POST" else request.GET
    return {c.value: src.get(c.value, "") for c in Collegamento}


@richiede_permesso(F.TRACCIATURA_REGISTRA)
def nuova(request: HttpRequest) -> HttpResponse:
    coll = _contesto_collegamenti(request)
    ordine = Ordine.objects.filter(pk=coll["ordine"]).first() if coll["ordine"].isdigit() else None
    modello = None
    sel = (request.POST if request.method == "POST" else request.GET).get("modello", "")
    if sel.isdigit():
        modello = get_object_or_404(Modello, pk=int(sel), attivo=True)
    if request.method == "POST" and modello is not None:
        form_ordine = OrdineForm(request.POST)
        coll["ordine"] = form_ordine.cleaned_data.get("ordine") if form_ordine.is_valid() else None
        try:
            t = servizi.registra(
                request.user,
                modello.pk,
                collegamenti=coll,
                valori=request.POST,
                files=request.FILES,
                note=request.POST.get("note", ""),
            )
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
        else:
            esito = {Esito.CONFORME: " Esito: conforme.", Esito.NON_CONFORME: " Esito: NON CONFORME."}.get(t.esito, "")
            (messages.warning if t.esito == Esito.NON_CONFORME else messages.success)(
                request, f"Tracciatura «{modello}» registrata.{esito}"
            )
            if request.POST.get("ancora"):
                altra = {k: v for k, v in coll.items() if k != "ordine" and v} | (
                    {"ordine": t.ordine_id} if t.ordine_id else {}
                )
                return redirect(
                    f"{reverse('tracciatura:nuova')}?modello={modello.pk}&"
                    + "&".join(f"{k}={v}" for k, v in altra.items())
                )
            return redirect(t)
    codici = [coll["componente"], coll["articolo"]]
    if ordine is not None:
        codici += list(ordine.righe.values_list("articolo", flat=True))
    if modello is None:
        proposti = servizi.modelli_per(codici)
        return render(
            request,
            "tracciatura/scegli.html",
            {
                "proposti": proposti,
                "coll": coll,
                "ordine": ordine,
                "specifici": {m.pk for m in servizi.modelli_per(codici, True)},
            },
        )
    return render(
        request,
        "tracciatura/nuova.html",
        {
            "m": modello,
            "campi": modello.campi.filter(attivo=True).order_by("ordine", "id"),
            "coll": coll,
            "form_ordine": OrdineForm(initial={"ordine": ordine.pk if ordine else (coll["ordine"] or None)}),
            "dati": request.POST if request.method == "POST" else {},
        },
    )


# ------------------------------------------------------------------------------------------------- dettaglio
@richiede_permesso(F.TRACCIATURA_VEDERE)
def dettaglio(request: HttpRequest, pk: int) -> HttpResponse:
    t = get_object_or_404(Tracciatura.objects.select_related("modello", "operatore", "ordine", "ddt"), pk=pk)
    if request.method == "POST":
        try:
            servizi.annulla(request.user, t.pk, request.POST.get("motivo", ""))
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Tracciatura annullata.")
        return redirect(t)
    valori = {v.campo_id: v for v in t.valori.select_related("campo")}
    righe = [(c, valori.get(c.pk)) for c in t.modello.campi.order_by("ordine", "id") if c.attivo or c.pk in valori]
    puo_annullare = not t.annullata and (
        ha_permesso(request.user, F.TRACCIATURA_CONFIGURA)
        or (t.operatore_id == request.user.pk and ha_permesso(request.user, F.TRACCIATURA_REGISTRA))
    )
    return render(
        request,
        "tracciatura/dettaglio.html",
        {**_permessi(request), "t": t, "righe": righe, "puo_annullare": puo_annullare},
    )


@require_GET
@richiede_permesso(F.TRACCIATURA_VEDERE)
def file(request: HttpRequest, pk: int) -> HttpResponse:
    v = get_object_or_404(Valore, pk=pk)
    if not v.file:
        raise Http404
    try:
        aperto = v.file.open("rb")
    except (FileNotFoundError, OSError) as exc:
        raise Http404("File non disponibile.") from exc
    r = FileResponse(
        aperto,
        content_type=v.tipo_file or "application/octet-stream",
        as_attachment=request.GET.get("scarica") == "1",
        filename=v.nome_file or "file",
    )
    r["X-Content-Type-Options"] = "nosniff"
    r["Cache-Control"] = "private, max-age=3600"
    return r


@require_GET
@richiede_permesso(F.TRACCIATURA_VEDERE)
def pannello(request: HttpRequest) -> HttpResponse:
    """Riquadro per le pagine di ordini e DDT: tracciature collegate e modelli previsti."""
    ordine_id, ddt_id = request.GET.get("ordine", ""), request.GET.get("ddt", "")
    qs = Tracciatura.objects.select_related("modello", "operatore").filter(annullata_il__isnull=True)
    codici: list[str] = []
    if ordine_id.isdigit():
        ordine = get_object_or_404(Ordine, pk=int(ordine_id))
        qs = qs.filter(ordine=ordine)
        codici = list(ordine.righe.values_list("articolo", flat=True))
    elif ddt_id.isdigit():
        qs = qs.filter(ddt_id=int(ddt_id))
    else:
        raise Http404
    previsti = servizi.modelli_per(codici, solo_specifici=True) if codici else []
    return render(
        request,
        "tracciatura/_pannello.html",
        {**_permessi(request), "tracciature": qs[:50], "previsti": previsti, "ordine_id": ordine_id, "ddt_id": ddt_id},
    )


# ------------------------------------------------------------------------------------------------ configurazione
@richiede_permesso(F.TRACCIATURA_CONFIGURA)
def modelli(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        try:
            m = servizi.salva_modello(request.user, None, {**request.POST.dict(), "attivo": True})
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Modello «{m}» creato: aggiungi i campi.")
            return redirect(m)
    from django.db.models import Count, Q

    qs = Modello.objects.annotate(
        n_campi=Count("campi", filter=Q(campi__attivo=True), distinct=True),
        n_tracciature=Count("tracciature", distinct=True),
    )
    return render(request, "tracciatura/modelli.html", {"modelli": qs, "categorie": Categoria.choices})


@richiede_permesso(F.TRACCIATURA_CONFIGURA)
def modello(request: HttpRequest, pk: int) -> HttpResponse:
    m = get_object_or_404(Modello, pk=pk)
    if request.method == "POST":
        p = request.POST
        azione = p.get("azione", "")
        try:
            if azione == "modello":
                servizi.salva_modello(request.user, m.pk, {**p.dict(), "attivo": "attivo" in p})
                messages.success(request, "Modello salvato.")
            elif azione == "campo":
                campo_id = int(p["campo"]) if p.get("campo", "").isdigit() else None
                c = servizi.salva_campo(
                    request.user,
                    m.pk,
                    campo_id,
                    {**p.dict(), "obbligatorio": "obbligatorio" in p, "attivo": "attivo" in p or campo_id is None},
                )
                messages.success(request, f"Campo «{c}» salvato.")
            elif azione in ("su", "giu"):
                servizi.sposta_campo(request.user, int(p["campo"]), 1 if azione == "giu" else -1)
            elif azione == "elimina":
                esito = servizi.elimina_campo(request.user, int(p["campo"]))
                messages.success(request, f"Campo {esito}.")
        except (KeyError, ValueError):
            messages.error(request, "Valori non validi.")
        except (OperazioneNonConsentita, Campo.DoesNotExist) as exc:
            messages.error(request, str(exc))
        return redirect(m)
    return render(
        request,
        "tracciatura/modello.html",
        {
            "m": m,
            "campi": m.campi.order_by("ordine", "id"),
            "categorie": Categoria.choices,
            "tipi": TipoCampo.choices,
            "collegamenti": [(c.value, c.label, m.uso(c.value)) for c in Collegamento],
            "usi": Uso.choices,
            "usati": set(Valore.objects.filter(campo__modello=m).values_list("campo_id", flat=True).distinct()),
        },
    )
