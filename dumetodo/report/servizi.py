"""Report sui dati di produzione (ex ``Reports/produzione.py`` di Metodo).

Una sola definizione di "ore": Σ(quantità × prezzo) / resa oraria (stessa della pianificazione).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Count, F, OuterRef, Q, QuerySet, Subquery, Sum
from django.utils import timezone

from dumetodo.anagrafica.models import Articolo
from dumetodo.comunicazioni.models import EmailInUscita, StatoEmail
from dumetodo.configurazione import servizi as config
from dumetodo.consegne.models import RigaConsegnata
from dumetodo.core.permessi import F as Fn
from dumetodo.core.permessi import ha_permesso
from dumetodo.importazione.models import ProblemaImportazione
from dumetodo.ordini.models import Ordine, RigaOrdine, StatoOrdine, StatoRiga
from dumetodo.ordini.servizi import OperazioneNonConsentita
from dumetodo.pianificazione import calendario, piano

from .models import OreLavorate

ZERO = Decimal(0)
APERTI = (StatoOrdine.INSERITO, StatoOrdine.IN_LAVORAZIONE, StatoOrdine.IN_SPEDIZIONE, StatoOrdine.EVASIONE_PARZIALE)


def _ore(valore: Decimal) -> Decimal | None:
    resa = config.leggi("produzione.prezzo_resa_oraria")
    return (valore / resa).quantize(Decimal("0.01")) if resa else None


def categoria(appendice: str) -> str:
    a = appendice.upper()
    if a == "CL":
        return "Conto lavoro"
    if "T" in a:
        return "Pulsanti (T)"
    if "R" in a:
        return "Elettronica (R)"
    return "Ordini Cliente" if not a else "Altro"


CATEGORIE = ("Pulsanti (T)", "Elettronica (R)", "Conto lavoro", "Ordini Cliente", "Altro")


def settimane_indietro(n: int, fino_a: dt.date | None = None) -> list[dt.date]:
    ultimo = calendario.lunedi(fino_a or timezone.localdate())
    return [ultimo - dt.timedelta(weeks=i) for i in range(n - 1, -1, -1)]


# ----------------------------------------------------------------------------------------- dashboard
def dashboard(oggi: dt.date | None = None) -> dict[str, Any]:
    oggi = oggi or timezone.localdate()
    per_stato = dict(Ordine.objects.filter(stato__in=APERTI).values_list("stato").annotate(n=Count("pk")))
    in_ritardo = Ordine.objects.filter(stato__in=APERTI, data_scadenza__lt=oggi)
    settimane = settimane_indietro(12, oggi)
    consegnate = (
        RigaOrdine.objects.filter(bolla_emessa=True, data_consegna__gte=settimane[0])
        .exclude(stato=StatoRiga.ANNULLATA)
        .values_list("data_consegna", "qta", "prezzo_unitario")
    )
    pezzi = defaultdict(lambda: ZERO)
    valore = defaultdict(lambda: ZERO)
    for data, qta, prezzo in consegnate:
        lun = calendario.lunedi(data)
        pezzi[lun] += qta
        valore[lun] += qta * (prezzo or 0)
    massimo = max(pezzi.values(), default=ZERO) or Decimal(1)
    p = piano.calcola()
    carico = sum((r.settimane[0].ore for r in p.righe), ZERO) if p.righe else ZERO
    capacita = sum((r.settimane[0].capacita or ZERO for r in p.righe), ZERO)
    return {
        "per_stato": [(StatoOrdine(s).label, per_stato.get(s, 0)) for s in APERTI],
        "in_ritardo": in_ritardo.count(),
        "prossime": Ordine.objects.filter(stato__in=APERTI, data_scadenza__gte=oggi).order_by("data_scadenza")[:10],
        "ritardi": in_ritardo.order_by("data_scadenza")[:10],
        "da_consegnare": Ordine.objects.filter(
            stato__in=[StatoOrdine.IN_SPEDIZIONE, StatoOrdine.EVASIONE_PARZIALE]
        ).count(),
        "senza_ddt": RigaConsegnata.objects.filter(ddt__isnull=True).count(),
        "problemi_importazione": ProblemaImportazione.objects.filter(gestito=False).count(),
        "email_in_errore": EmailInUscita.objects.filter(stato__in=[StatoEmail.ERRORE, StatoEmail.FALLITA]).count(),
        "settimane": [
            {
                "lunedi": s,
                "pezzi": pezzi[s],
                "valore": valore[s].quantize(Decimal("0.01")),
                "percentuale": int(pezzi[s] / massimo * 100),
            }
            for s in settimane
        ],
        "carico": carico,
        "capacita": capacita,
        "saturazione": int(carico / capacita * 100) if capacita else None,
    }


# ----------------------------------------------------------------------------------------- produttività
@dataclass
class SettimanaProduttivita:
    lunedi: dt.date
    pezzi: dict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    valore: Decimal = ZERO
    ore_lavorate: Decimal | None = None

    @property
    def per_categoria(self) -> list[Decimal]:
        return [self.pezzi.get(c, ZERO) for c in CATEGORIE]

    @property
    def pezzi_totali(self) -> Decimal:
        return sum(self.pezzi.values(), ZERO)

    @property
    def ore_teoriche(self) -> Decimal | None:
        return _ore(self.valore)

    @property
    def pezzi_ora(self) -> Decimal | None:
        return (self.pezzi_totali / self.ore_lavorate).quantize(Decimal("0.01")) if self.ore_lavorate else None

    @property
    def efficienza(self) -> Decimal | None:
        """Ore teoriche consegnate / ore lavorate."""
        t = self.ore_teoriche
        return (t / self.ore_lavorate * 100).quantize(Decimal("0.1")) if t is not None and self.ore_lavorate else None


def _righe_consegnate(dal: dt.date, al: dt.date) -> QuerySet[RigaOrdine]:
    """Righe consegnate nel periodo; escluse annullate e scorporate (come l'analisi di Metodo)."""
    return RigaOrdine.objects.filter(bolla_emessa=True, data_consegna__range=(dal, al), scorporata=False).exclude(
        stato=StatoRiga.ANNULLATA
    )


def produttivita(settimane: list[dt.date]) -> list[SettimanaProduttivita]:
    risultato = {s: SettimanaProduttivita(s) for s in settimane}
    dal, al = settimane[0], settimane[-1] + dt.timedelta(days=6)
    for data, qta, prezzo, appendice in _righe_consegnate(dal, al).values_list(
        "data_consegna", "qta", "prezzo_unitario", "ordine__appendice"
    ):
        s = risultato[calendario.lunedi(data)]
        s.pezzi[categoria(appendice)] += qta
        s.valore += qta * (prezzo or 0)
    for ol in OreLavorate.objects.filter(settimana__in=settimane):
        risultato[ol.settimana].ore_lavorate = ol.ore
    return list(risultato.values())


def produttivita_giornaliera(lunedi: dt.date) -> list[dict[str, Any]]:
    giorni = {lunedi + dt.timedelta(days=i): defaultdict(lambda: ZERO) for i in range(7)}
    for data, qta, appendice in _righe_consegnate(lunedi, lunedi + dt.timedelta(days=6)).values_list(
        "data_consegna", "qta", "ordine__appendice"
    ):
        giorni[data][categoria(appendice)] += qta
    return [
        {"data": d, "per_categoria": [p.get(c, ZERO) for c in CATEGORIE], "totale": sum(p.values(), ZERO)}
        for d, p in giorni.items()
    ]


@transaction.atomic
def salva_ore_lavorate(lunedi: dt.date, ore: str, utente: Any, note: str = "") -> OreLavorate | None:
    if not ha_permesso(utente, Fn.REPORT_PRODUTTIVITA):
        raise OperazioneNonConsentita("Non hai i permessi per inserire le ore lavorate.")
    lunedi = calendario.lunedi(lunedi)
    testo = (ore or "").strip().replace(",", ".")
    if not testo:
        OreLavorate.objects.filter(settimana=lunedi).delete()
        return None
    try:
        valore = Decimal(testo)
    except ArithmeticError:
        raise OperazioneNonConsentita(f"Ore non valide: {ore!r}.") from None
    if not valore.is_finite() or valore < 0 or valore > 100000:
        raise OperazioneNonConsentita("Le ore devono essere un numero tra 0 e 100000.")
    obj, _ = OreLavorate.objects.update_or_create(
        settimana=lunedi, defaults={"ore": valore, "note": note[:200], "aggiornato_da": utente}
    )
    return obj


# ----------------------------------------------------------------------------------------- materiale
def materiale_per_reparto(settimane: list[dt.date]) -> tuple[list[dict[str, Any]], list[dt.date]]:
    """Per reparto e settimana: ore 'a scadenza' (righe con scadenza nella settimana) e ore 'consegnate'
    (righe consegnate nella settimana). Righe annullate/sostituite escluse."""
    dal, al = settimane[0], settimane[-1] + dt.timedelta(days=6)
    reparto_di = dict(Articolo.objects.values_list("codice", "reparto__nome"))
    dati: dict[str, dict[dt.date, dict[str, Decimal]]] = defaultdict(
        lambda: {s: {"scadenza": ZERO, "consegnato": ZERO} for s in settimane}
    )
    righe = RigaOrdine.objects.filter(stato=StatoRiga.ATTIVA).exclude(prezzo_unitario=None)
    for art, qta, prezzo, data in righe.filter(data_scadenza__range=(dal, al)).values_list(
        "articolo", "qta", "prezzo_unitario", "data_scadenza"
    ):
        dati[reparto_di.get(art) or "Senza reparto"][calendario.lunedi(data)]["scadenza"] += qta * prezzo
    for art, qta, prezzo, data in righe.filter(bolla_emessa=True, data_consegna__range=(dal, al)).values_list(
        "articolo", "qta", "prezzo_unitario", "data_consegna"
    ):
        dati[reparto_di.get(art) or "Senza reparto"][calendario.lunedi(data)]["consegnato"] += qta * prezzo
    righe_out = []
    for nome in sorted(dati, key=lambda n: (n == "Senza reparto", n)):
        celle = [{"scadenza": _ore(v["scadenza"]), "consegnato": _ore(v["consegnato"])} for v in dati[nome].values()]
        righe_out.append(
            {
                "reparto": nome,
                "celle": celle,
                "tot_scadenza": _ore(sum((v["scadenza"] for v in dati[nome].values()), ZERO)),
                "tot_consegnato": _ore(sum((v["consegnato"] for v in dati[nome].values()), ZERO)),
            }
        )
    return righe_out, settimane


def evaso_consegnato_per_settimana(settimane: list[dt.date]) -> dict[str, Any]:
    """Una riga per settimana (dalla più recente) e una colonna per reparto: ore in scadenza e ore consegnate.

    Stessi dati di ``materiale_per_reparto``, girati: ore = valore / resa oraria."""
    per_reparto, _ = materiale_per_reparto(settimane)
    reparti = [r["reparto"] for r in per_reparto]
    righe = []
    for i, lunedi in reversed(list(enumerate(settimane))):
        celle = [r["celle"][i] for r in per_reparto]
        righe.append(
            {
                "lunedi": lunedi,
                "domenica": lunedi + dt.timedelta(days=6),
                "celle": celle,
                "tot_scadenza": _somma(c["scadenza"] for c in celle),
                "tot_consegnato": _somma(c["consegnato"] for c in celle),
            }
        )
    totali = [{"scadenza": r["tot_scadenza"], "consegnato": r["tot_consegnato"]} for r in per_reparto]
    return {
        "reparti": reparti,
        "righe": righe,
        "totali": totali,
        "totale_scadenza": _somma(t["scadenza"] for t in totali),
        "totale_consegnato": _somma(t["consegnato"] for t in totali),
        "resa_configurata": bool(config.leggi("produzione.prezzo_resa_oraria")),
    }


def _somma(valori: Any) -> Decimal | None:
    lista = [v for v in valori if v is not None]
    return sum(lista, ZERO) if lista else None


# ----------------------------------------------------------------------------------------- controllo prezzi
@dataclass
class FiltriPrezzi:
    origine: str = "bolle"  # "bolle" | "lavorazione"
    dal: dt.date | None = None
    al: dt.date | None = None
    articolo: str = ""
    numero_ddt: str = ""
    solo_differenze: bool = True
    senza_riferimento: bool = False


def controllo_prezzi(f: FiltriPrezzi) -> QuerySet[RigaOrdine]:
    """Confronto tra il prezzo della riga e il prezzo di riferimento in anagrafica.

    - bolle: righe consegnate nel periodo (data di consegna), con il numero di DDT se registrato;
    - lavorazione: righe non ancora in bolla di ordini aperti (Inserito, Lavorato, In spedizione, Parziale),
      per accorgersi di un prezzo sbagliato PRIMA che vada in bolla (in Metodo era un report separato)."""
    rif = Articolo.objects.filter(codice=OuterRef("articolo")).values("prezzo_riferimento")[:1]
    qs = RigaOrdine.objects.filter(stato=StatoRiga.ATTIVA).select_related("ordine")
    if f.origine == "lavorazione":
        qs = qs.filter(bolla_emessa=False, ordine__stato__in=APERTI)
        campo_data = "data_scadenza"
    else:
        qs = qs.filter(bolla_emessa=True)
        campo_data = "data_consegna"
        if f.numero_ddt:
            qs = qs.filter(consegnata__ddt__numero=f.numero_ddt.strip())
    if f.dal:
        qs = qs.filter(**{f"{campo_data}__gte": f.dal})
    if f.al:
        qs = qs.filter(**{f"{campo_data}__lte": f.al})
    if f.articolo:
        qs = qs.filter(articolo__icontains=f.articolo.strip())
    qs = qs.annotate(
        prezzo_rif=Subquery(rif), ddt_numero=F("consegnata__ddt__numero"), ddt_anno=F("consegnata__ddt__anno")
    )
    if f.senza_riferimento:
        qs = qs.filter(prezzo_rif__isnull=True)
    elif f.solo_differenze:
        qs = qs.filter(prezzo_rif__isnull=False).filter(~Q(prezzo_unitario=F("prezzo_rif")) | Q(prezzo_unitario=None))
    qs = qs.annotate(
        differenza=F("prezzo_unitario") - F("prezzo_rif"),
        differenza_totale=(F("prezzo_unitario") - F("prezzo_rif")) * F("qta"),
    )
    return qs.order_by(f"-{campo_data}", "ordine__numero", "n_riga")


def totale_differenze(qs: QuerySet[RigaOrdine]) -> Decimal:
    return qs.aggregate(t=Sum("differenza_totale"))["t"] or ZERO
