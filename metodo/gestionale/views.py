import datetime as dt
from decimal import Decimal, InvalidOperation
from itertools import groupby
from typing import Any

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from metodo.configurazione import servizi as config
from metodo.configurazione.forms import ParametriGruppoForm
from metodo.configurazione.models import ValoreParametro
from metodo.configurazione.registro import GRUPPI_GESTIONALE
from metodo.consegne.models import DDT, OrigineDDT
from metodo.core.permessi import F, ha_permesso, richiede_permesso
from metodo.ordini.servizi import OperazioneNonConsentita

from . import servizi
from .costanti import CAUSALI_TRASPORTO, MODALITA_PAGAMENTO, PORTI, TRASPORTO_A_CURA
from .models import Fattura, Numerazione, StatoFattura, StatoSdI, TipoNumerazione


def _data(valore: str, predefinita: dt.date | None = None) -> dt.date:
    try:
        return dt.date.fromisoformat(valore)
    except (TypeError, ValueError):
        if predefinita is None:
            raise
        return predefinita


def _numerazione(request: HttpRequest) -> int | None:
    v = request.POST.get("numerazione", "")
    return int(v) if v.isdigit() else None


def _permessi(request: HttpRequest) -> dict[str, bool]:
    u = request.user
    return {
        "puo_ddt": ha_permesso(u, F.GESTIONALE_DDT),
        "puo_fatture": ha_permesso(u, F.GESTIONALE_FATTURE),
        "puo_impostazioni": ha_permesso(u, F.GESTIONALE_IMPOSTAZIONI),
    }


@require_GET
@richiede_permesso(F.GESTIONALE_VEDERE)
def indice(request: HttpRequest) -> HttpResponse:
    anno = timezone.localdate().year
    return render(
        request,
        "gestionale/indice.html",
        {
            **_permessi(request),
            "righe_da_emettere": servizi.righe_da_emettere().count(),
            "ddt_da_fatturare": servizi.ddt_da_fatturare().count(),
            "bozze": Fattura.objects.filter(stato=StatoFattura.BOZZA).count(),
            "da_inviare": Fattura.objects.filter(stato=StatoFattura.EMESSA, stato_sdi=StatoSdI.DA_INVIARE).count(),
            "scartate": Fattura.objects.filter(stato_sdi=StatoSdI.SCARTATA).count(),
            "ddt_anno": DDT.objects.filter(origine=OrigineDDT.INTERNO, anno=anno, annullato_il=None).count(),
            "fatture_anno": Fattura.objects.filter(stato=StatoFattura.EMESSA, anno=anno).exclude(tipo="TD04").count(),
            "anno": anno,
            "problemi_ddt": servizi.problemi_dati_ddt(),
            "problemi_fatture": servizi.problemi_dati_fattura(),
        },
    )


# ------------------------------------------------------------------------------------------------ DDT
@richiede_permesso(F.GESTIONALE_DDT)
def ddt_emetti(request: HttpRequest) -> HttpResponse:
    valori = servizi.valori_predefiniti_ddt()
    if request.method == "POST":
        valori, d = emetti_da_richiesta(request)
        if d is not None:
            return redirect("gestionale:ddt", d.pk)
    righe = list(servizi.righe_da_emettere())
    gruppi = [(o, list(rr)) for o, rr in groupby(righe, key=lambda rc: rc.riga.ordine)]
    return render(
        request,
        "gestionale/ddt_emetti.html",
        {"gruppi": gruppi, "n_righe": len(righe), **contesto_trasporto(valori)},
    )


CAMPI_TRASPORTO = (
    "causale",
    "aspetto_beni",
    "trasporto_a_cura",
    "porto",
    "vettore",
    "vettore_indirizzo",
    "note",
    "colli",
    "peso_kg",
)


def emetti_da_richiesta(request: HttpRequest) -> tuple[dict[str, Any], Any]:
    """Emette il DDT dai dati del modulo (usato da Gestionale e da Spedizioni → DDT). Ritorna valori e DDT (o None)."""
    p = request.POST
    valori: dict[str, Any] = {k: p.get(k, "") for k in CAMPI_TRASPORTO}
    try:
        data = _data(p.get("data", ""))
        colli = int(p["colli"]) if p.get("colli", "").strip() else None
        peso = Decimal(p["peso_kg"].replace(",", ".")) if p.get("peso_kg", "").strip() else None
        ids = [int(i) for i in p.getlist("riga") if i.isdigit()]
        d = servizi.emetti_ddt(
            request.user, ids, data, {**valori, "colli": colli, "peso_kg": peso}, _numerazione(request)
        )
    except (ValueError, InvalidOperation):
        messages.error(request, "Controlla data, colli e peso.")
    except OperazioneNonConsentita as exc:
        messages.error(request, str(exc))
    else:
        avviso = "" if d.email else " Il file tecnico NON è stato inviato: mancano i destinatari in Configurazione."
        (messages.success if d.email else messages.warning)(request, f"Emesso il DDT n. {d.numero}/{d.anno}.{avviso}")
        return valori, d
    return valori, None


def contesto_trasporto(valori: dict[str, Any]) -> dict[str, Any]:
    """Dati per il riquadro «Dati del trasporto» (gestionale/_dati_trasporto.html)."""
    oggi = timezone.localdate()
    numerazioni = list(servizi.numerazioni(TipoNumerazione.DDT))
    return {
        "valori": valori,
        "oggi": oggi.isoformat(),
        "causali": CAUSALI_TRASPORTO,
        "trasporti": TRASPORTO_A_CURA,
        "porti": PORTI,
        "problemi": servizi.problemi_dati_ddt(),
        "numerazioni": numerazioni,
        "prossimi": [(n, n.numero(servizi.prossimo_previsto(n, oggi.year), oggi.year)) for n in numerazioni],
        "destinatario": servizi.dati_cliente(),
        "destinazione": config.leggi("gestionale.cliente_destinazione") or "",
    }


@require_GET
@richiede_permesso(F.GESTIONALE_VEDERE)
def ddt_lista(request: HttpRequest) -> HttpResponse:
    anno = request.GET.get("anno", "")
    origine = request.GET.get("origine", "")
    qs = DDT.objects.defer("file_contenuto").annotate(n_righe=Count("righe")).order_by("-anno", "-data", "-id")
    if anno.isdigit():
        qs = qs.filter(anno=int(anno))
    if origine in OrigineDDT.values:
        qs = qs.filter(origine=origine)
    anni = DDT.objects.values_list("anno", flat=True).distinct().order_by("-anno")
    return render(
        request,
        "gestionale/ddt_lista.html",
        {
            **_permessi(request),
            "pagina": Paginator(qs, 50).get_page(request.GET.get("pagina")),
            "anni": anni,
            "anno": anno,
            "origine": origine,
            "origini": OrigineDDT.choices,
        },
    )


@richiede_permesso(F.GESTIONALE_VEDERE)
def ddt(request: HttpRequest, pk: int) -> HttpResponse:
    d = get_object_or_404(DDT.objects.defer("file_contenuto"), pk=pk)
    if request.method == "POST":
        try:
            servizi.annulla_ddt(request.user, d.pk, request.POST.get("motivo", ""))
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"DDT n. {d.numero}/{d.anno} annullato: le righe tornano tra quelle da emettere.")
        return redirect("gestionale:ddt", d.pk)
    righe_fattura = d.righe_fattura.select_related("fattura").exclude(fattura__tipo="TD04")
    fatture = sorted({r.fattura for r in righe_fattura}, key=lambda f: f.pk)
    return render(
        request,
        "gestionale/ddt.html",
        {
            **_permessi(request),
            "d": d,
            "righe": d.righe.select_related("riga__ordine").order_by(
                "riga__ordine__serie", "riga__ordine__numero", "riga__n_riga"
            ),
            "fatture": fatture,
        },
    )


@require_GET
@richiede_permesso(F.GESTIONALE_VEDERE, F.DDT)  # la stampa accompagna la merce: serve anche in Spedizioni
def ddt_stampa(request: HttpRequest, pk: int) -> HttpResponse:
    d = get_object_or_404(DDT.objects.defer("file_contenuto"), pk=pk, origine=OrigineDDT.INTERNO)
    return render(request, "gestionale/ddt_stampa.html", {"d": d, "doc": d.documento})


# -------------------------------------------------------------------------------------------- fatture
@richiede_permesso(F.GESTIONALE_FATTURE)
def da_fatturare(request: HttpRequest) -> HttpResponse:
    oggi = timezone.localdate()
    if request.method == "POST":
        p = request.POST
        try:
            tipo_data = p.get("tipo_data", "scelta")
            data = _data(p.get("data", "")) if tipo_data == "scelta" else None
            max_righe = int(p["max_righe"]) if p.get("max_righe", "").strip() else None
            ids = [int(i) for i in p.getlist("ddt") if i.isdigit()]
            bozze = servizi.crea_fatture_differite(
                request.user,
                ids,
                raggruppa=p.get("raggruppa", "mese"),
                max_righe=max_righe,
                tipo_data=tipo_data,
                data=data,
            )
        except ValueError:
            messages.error(request, "Controlla la data e il numero di righe.")
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
        else:
            if len(bozze) == 1:
                messages.success(request, "Bozza creata: controlla righe e totali, poi emetti la fattura.")
                return redirect(bozze[0])
            messages.success(
                request,
                f"Create {len(bozze)} bozze, dal {bozze[0].data:%d/%m/%Y} al {bozze[-1].data:%d/%m/%Y}: "
                "controllale ed emettile (anche tutte insieme, in ordine di data).",
            )
            return redirect(f"{reverse('gestionale:fatture')}?stato=BOZ")
    ddts = list(servizi.ddt_da_fatturare().annotate(n_righe=Count("righe")))
    mesi = []
    for (anno, mese), gruppo in groupby(ddts, key=lambda d: (d.data.year, d.data.month)):
        lista = list(gruppo)
        ultimo = max(d.data for d in lista)
        termine = servizi.scadenza_fattura_differita(ultimo)
        mesi.append(
            {
                "chiave": f"{anno}-{mese:02d}",
                "anno": anno,
                "mese": dt.date(anno, mese, 1),
                "ddt": lista,
                "data_proposta": min(oggi, termine).isoformat() if oggi >= ultimo else ultimo.isoformat(),
                "termine": termine,
                "in_ritardo": oggi > termine,
            }
        )
    return render(
        request,
        "gestionale/da_fatturare.html",
        {"mesi": mesi, "raggruppamenti": servizi.RAGGRUPPAMENTI, "date_fattura": servizi.DATE_FATTURA},
    )


@richiede_permesso(F.GESTIONALE_VEDERE)
def fatture(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        ids = [int(i) for i in request.POST.getlist("bozza") if i.isdigit()]
        try:
            emesse, problema = servizi.emetti_bozze(request.user, ids, _numerazione(request))
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
        else:
            if emesse:
                messages.success(
                    request, f"Emesse {len(emesse)} fatture: dalla n. {emesse[0].numero} alla n. {emesse[-1].numero}."
                )
            if problema:
                messages.error(request, f"Emissione interrotta. {problema}")
        return redirect(request.get_full_path())
    qs = Fattura.objects.select_related("fattura_collegata").order_by("-anno", "-progressivo", "-id")
    anno, stato, sdi = request.GET.get("anno", ""), request.GET.get("stato", ""), request.GET.get("sdi", "")
    if anno.isdigit():
        qs = qs.filter(anno=int(anno))
    if stato in StatoFattura.values:
        qs = qs.filter(stato=stato)
    if sdi in StatoSdI.values:
        qs = qs.filter(stato=StatoFattura.EMESSA, stato_sdi=sdi)
    return render(
        request,
        "gestionale/fatture.html",
        {
            "pagina": Paginator(qs, 50).get_page(request.GET.get("pagina")),
            "anni": Fattura.objects.values_list("anno", flat=True).distinct().order_by("-anno"),
            "anno": anno,
            "stato": stato,
            "sdi": sdi,
            "stati": StatoFattura.choices,
            "stati_sdi": StatoSdI.choices,
            "puo_fatture": ha_permesso(request.user, F.GESTIONALE_FATTURE),
            "numerazioni": servizi.numerazioni(TipoNumerazione.FATTURA),
        },
    )


@richiede_permesso(F.GESTIONALE_VEDERE)
def fattura(request: HttpRequest, pk: int) -> HttpResponse:
    f = get_object_or_404(Fattura.objects.select_related("fattura_collegata", "creata_da", "emessa_da"), pk=pk)
    if request.method == "POST":
        azione = request.POST.get("azione", "")
        try:
            if azione == "emetti":
                f = servizi.emetti_fattura(request.user, f.pk, _numerazione(request))
                messages.success(request, f"{f} emessa: scarica l'XML e invialo allo SdI.")
            elif azione == "modifica":
                servizi.modifica_bozza(
                    request.user, f.pk, _data(request.POST.get("data", "")), request.POST.get("causale", "")
                )
                messages.success(request, "Bozza aggiornata.")
            elif azione == "elimina":
                servizi.elimina_bozza(request.user, f.pk)
                messages.success(request, "Bozza eliminata: i DDT tornano da fatturare.")
                return redirect("gestionale:fatture")
            elif azione == "nota_di_credito":
                nota = servizi.nota_di_credito(request.user, f.pk)
                messages.success(request, "Nota di credito in bozza: per uno storno parziale riduci le quantità.")
                return redirect(nota)
            elif azione == "riga":
                servizi.riduci_riga_nota(
                    request.user,
                    int(request.POST.get("riga", "0")),
                    Decimal(request.POST.get("qta", "").replace(",", ".")),
                )
            elif azione == "sdi":
                servizi.aggiorna_stato_sdi(
                    request.user, f.pk, request.POST.get("stato_sdi", ""), request.POST.get("note_sdi", "")
                )
                messages.success(request, "Stato dell'invio aggiornato.")
        except (ValueError, InvalidOperation):
            messages.error(request, "Valore non valido.")
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
        return redirect(f)
    righe = list(f.righe.select_related("ddt"))
    return render(
        request,
        "gestionale/fattura.html",
        {
            **_permessi(request),
            "f": f,
            "righe": righe,
            "ddt": sorted({r.ddt for r in righe if r.ddt}, key=lambda d: (d.data, d.pk)),
            "riepiloghi": servizi.fatturapa.riepiloghi(servizi.documento(f, numero="1", progressivo_invio="00001")),
            "errori": servizi.anteprima_errori(f) if f.is_bozza else [],
            "note_di_credito": f.note_di_credito.all(),
            "stati_sdi": StatoSdI.choices,
            "numerazioni": servizi.numerazioni(TipoNumerazione.FATTURA),
        },
    )


@require_GET
@richiede_permesso(F.GESTIONALE_VEDERE)
def fattura_xml(request: HttpRequest, pk: int) -> HttpResponse:
    f = get_object_or_404(Fattura, pk=pk, stato=StatoFattura.EMESSA)
    r = HttpResponse(f.xml.encode("utf-8"), content_type="application/xml; charset=utf-8")
    r["Content-Disposition"] = f'attachment; filename="{f.xml_nome}"'
    return r


@require_GET
@richiede_permesso(F.GESTIONALE_VEDERE)
def fattura_stampa(request: HttpRequest, pk: int) -> HttpResponse:
    f = get_object_or_404(Fattura.objects.select_related("fattura_collegata"), pk=pk)
    righe = list(f.righe.select_related("ddt"))
    doc = servizi.documento(f, numero=f.numero or "bozza", progressivo_invio=f.progressivo_invio or "00000")
    return render(
        request,
        "gestionale/fattura_stampa.html",
        {
            "f": f,
            "righe": righe,
            "cedente": f.cedente or servizi.dati_fornitore(),
            "cessionario": f.cessionario or servizi.dati_cliente(),
            "riepiloghi": servizi.fatturapa.riepiloghi(doc),
            "ddt": sorted({r.ddt for r in righe if r.ddt}, key=lambda d: (d.data, d.pk)),
            "bollo": servizi.fatturapa.bollo_dovuto(doc),
            "pagamento": f.pagamento,
            "modalita_pagamento": dict(MODALITA_PAGAMENTO)
            .get((f.pagamento or {}).get("modalita", ""), "")
            .split("– ")[-1],
        },
    )


# ---------------------------------------------------------------------------------------- impostazioni
@richiede_permesso(F.GESTIONALE_IMPOSTAZIONI)
def impostazioni(request: HttpRequest) -> HttpResponse:
    attivo = request.POST.get("gruppo") if request.method == "POST" else None
    forms = []
    for gruppo in GRUPPI_GESTIONALE:
        if attivo == gruppo.name:
            form = ParametriGruppoForm(gruppo, request.POST)
            if form.is_valid():
                config.scrivi_molti({p.chiave: form.cleaned_data[p.chiave] for p in form.parametri}, request.user)
                messages.success(request, f"«{gruppo.value}» salvati.")
                return redirect(f"{reverse('gestionale:impostazioni')}#gruppo-{gruppo.name}")
            messages.error(request, "Correggi gli errori evidenziati: nessun valore è stato salvato.")
        else:
            form = ParametriGruppoForm(gruppo)
        forms.append(form)
    storico = (
        ValoreParametro.history.filter(chiave__startswith="gestionale.")
        .select_related("history_user")
        .order_by("-history_date")[:50]
    )
    return render(
        request,
        "gestionale/impostazioni.html",
        {
            "forms": forms,
            "storico": storico,
            "problemi_ddt": servizi.problemi_dati_ddt(),
            "problemi_fatture": servizi.problemi_dati_fattura(),
        },
    )


@richiede_permesso(F.GESTIONALE_IMPOSTAZIONI, F.CONFIGURAZIONE)
def numerazioni(request: HttpRequest) -> HttpResponse:
    """Numerazioni di DDT e fatture. Da Configurazione (senza i permessi del Gestionale) solo quelle dei DDT."""
    anno = timezone.localdate().year
    tutte = ha_permesso(request.user, F.GESTIONALE_IMPOSTAZIONI)
    tipi = TipoNumerazione.choices if tutte else [(TipoNumerazione.DDT, TipoNumerazione.DDT.label)]
    if request.method == "POST":
        p = request.POST
        try:
            if not tutte and (
                p.get("tipo", TipoNumerazione.DDT) != TipoNumerazione.DDT
                or (
                    p.get("numerazione", "").isdigit()
                    and not Numerazione.objects.filter(pk=int(p["numerazione"]), tipo=TipoNumerazione.DDT).exists()
                )
            ):
                raise OperazioneNonConsentita("Da Configurazione si gestiscono solo le numerazioni dei DDT.")
            if p.get("azione") == "prossimo":
                n = servizi.imposta_prossimo(
                    request.user, int(p["numerazione"]), int(p["anno"]), int(p.get("prossimo", "0"))
                )
                messages.success(
                    request,
                    f"«{n.numerazione}» {n.anno}: il prossimo numero sarà "
                    f"{n.numerazione.numero(n.ultimo + 1, n.anno)}.",
                )
            else:
                pk = int(p["numerazione"]) if p.get("numerazione", "").isdigit() else None
                n = servizi.salva_numerazione(
                    request.user,
                    pk,
                    tipo=p.get("tipo", ""),
                    nome=p.get("nome", ""),
                    formato=p.get("formato", ""),
                    predefinita=bool(p.get("predefinita")),
                    attiva=bool(p.get("attiva")) if pk else True,
                )
                messages.success(request, f"Numerazione «{n}» salvata.")
        except (KeyError, ValueError):
            messages.error(request, "Valori non validi.")
        except (OperazioneNonConsentita, Numerazione.DoesNotExist) as exc:
            messages.error(request, str(exc))
        return redirect(request.path)
    righe = []
    for n in Numerazione.objects.filter(tipo__in=[t for t, _ in tipi]):
        righe.append(
            {
                "n": n,
                "anni": [
                    {"anno": a, "usato": servizi.ultimo_usato(n, a), "prossimo": servizi.prossimo_previsto(n, a)}
                    for a in (anno, anno + 1)
                ],
                "esempio": n.numero(1, anno),
            }
        )
    return render(
        request,
        "gestionale/numerazioni.html",
        {"righe": righe, "tipi": tipi, "anno": anno, "solo_ddt": not tutte},
    )
