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

from metodo.anagrafica.models import Articolo
from metodo.comunicazioni.models import EmailInUscita, StatoEmail
from metodo.configurazione import servizi as config
from metodo.consegne.models import RigaConsegnata
from metodo.consegne.servizi import ordini_da_consegnare
from metodo.core.permessi import F as Fn
from metodo.core.permessi import ha_permesso
from metodo.importazione.models import ProblemaImportazione
from metodo.ordini.models import Ordine, RigaOrdine, StatoOrdine, StatoRiga
from metodo.ordini.servizi import OperazioneNonConsentita
from metodo.pianificazione import calendario, piano

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


Committenti = list[int] | None  # id dei clienti committenti da considerare; None = tutti


def _righe(committenti: Committenti) -> QuerySet[RigaOrdine]:
    qs = RigaOrdine.objects.all()
    return qs if committenti is None else qs.filter(ordine__committente__in=committenti)


def _ordini(committenti: Committenti) -> QuerySet[Ordine]:
    qs = Ordine.objects.all()
    return qs if committenti is None else qs.filter(committente__in=committenti)


def settimane_indietro(n: int, fino_a: dt.date | None = None) -> list[dt.date]:
    ultimo = calendario.lunedi(fino_a or timezone.localdate())
    return [ultimo - dt.timedelta(weeks=i) for i in range(n - 1, -1, -1)]


# ----------------------------------------------------------------------------------------- dashboard
def dashboard(oggi: dt.date | None = None, committenti: Committenti = None, parziale: bool = False) -> dict[str, Any]:
    oggi = oggi or timezone.localdate()
    ordini = _ordini(committenti)
    per_stato = dict(ordini.filter(stato__in=APERTI).values_list("stato").annotate(n=Count("pk")))
    in_ritardo = ordini.filter(stato__in=APERTI, data_scadenza__lt=oggi)
    settimane = settimane_indietro(12, oggi)
    consegnate = (
        _righe(committenti)
        .filter(bolla_emessa=True, data_consegna__gte=settimane[0])
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
    # Ore lavorate della settimana: dalle timbrature, o la correzione a mano dell'Analisi produttività.
    # Sono di tutta l'azienda: con un solo committente l'efficienza non sarebbe confrontabile.
    ore = {lunedi: v["totale"] for lunedi, v in ore_timbrate(settimane).items()}
    ore.update(OreLavorate.objects.filter(settimana__in=settimane).values_list("settimana", "ore"))
    p = piano.calcola(committenti=committenti)
    carico = sum((r.settimane[0].ore for r in p.righe), ZERO) if p.righe else ZERO
    capacita = sum((r.settimane[0].capacita or ZERO for r in p.righe), ZERO)
    return {
        "per_stato": [(StatoOrdine(s).label, per_stato.get(s, 0)) for s in APERTI],
        "in_ritardo": in_ritardo.count(),
        "prossime": ordini.filter(stato__in=APERTI, data_scadenza__gte=oggi).order_by("data_scadenza")[:10],
        "ritardi": in_ritardo.order_by("data_scadenza")[:10],
        "da_consegnare": ordini_da_consegnare().filter(pk__in=ordini.values("pk")).count(),
        "senza_ddt": RigaConsegnata.objects.filter(ddt__isnull=True, riga__in=_righe(committenti)).count(),
        "problemi_importazione": ProblemaImportazione.objects.filter(gestito=False).count(),
        "email_in_errore": EmailInUscita.objects.filter(stato__in=[StatoEmail.ERRORE, StatoEmail.FALLITA]).count(),
        "settimane": [
            {
                "lunedi": s,
                "pezzi": pezzi[s],
                "valore": valore[s].quantize(Decimal("0.01")),
                "percentuale": int(pezzi[s] / massimo * 100),
                "ore": ore.get(s),
                "efficienza": _percentuale(_ore(valore[s]), ore.get(s)) if ore.get(s) and not parziale else None,
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
    # Con un solo committente le ore lavorate (di tutta l'azienda) non sono confrontabili con i suoi pezzi.
    parziale: bool = False
    ore_timbrate: Decimal | None = None  # dalle timbrature (presenza meno mensa e pausa), in automatico
    ore_manuali: Decimal | None = None  # correzione scritta a mano: se c'è, vale questa

    @property
    def ore_lavorate(self) -> Decimal | None:
        return self.ore_manuali if self.ore_manuali is not None else self.ore_timbrate

    @ore_lavorate.setter
    def ore_lavorate(self, valore: Decimal | None) -> None:
        self.ore_manuali = valore

    @property
    def fonte_ore(self) -> str:
        if self.ore_manuali is not None:
            return "corrette a mano"
        return "dalle timbrature" if self.ore_timbrate is not None else ""

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
        if self.parziale or not self.ore_lavorate:
            return None
        return (self.pezzi_totali / self.ore_lavorate).quantize(Decimal("0.01"))

    @property
    def resa_oraria(self) -> Decimal | None:
        """Resa oraria reale in €/ora: valore consegnato / ore lavorate (da confrontare con quella di riferimento)."""
        if self.parziale or not self.ore_lavorate:
            return None
        return (self.valore / self.ore_lavorate).quantize(Decimal("0.01"))

    @property
    def efficienza(self) -> Decimal | None:
        """Ore teoriche consegnate / ore lavorate."""
        t = self.ore_teoriche
        if self.parziale:
            return None
        return (t / self.ore_lavorate * 100).quantize(Decimal("0.1")) if t is not None and self.ore_lavorate else None


def _righe_consegnate(dal: dt.date, al: dt.date, committenti: Committenti = None) -> QuerySet[RigaOrdine]:
    """Righe consegnate nel periodo; escluse annullate e scorporate (come l'analisi di Metodo)."""
    return (
        _righe(committenti)
        .filter(bolla_emessa=True, data_consegna__range=(dal, al), scorporata=False)
        .exclude(stato=StatoRiga.ANNULLATA)
    )


def produttivita(
    settimane: list[dt.date], committenti: Committenti = None, parziale: bool = False
) -> list[SettimanaProduttivita]:
    """``parziale``: i committenti sono solo una parte dell'azienda (niente efficienza né pezzi/ora)."""
    risultato = {s: SettimanaProduttivita(s, parziale=parziale) for s in settimane}
    dal, al = settimane[0], settimane[-1] + dt.timedelta(days=6)
    for data, qta, prezzo, appendice in _righe_consegnate(dal, al, committenti).values_list(
        "data_consegna", "qta", "prezzo_unitario", "ordine__appendice"
    ):
        s = risultato[calendario.lunedi(data)]
        s.pezzi[categoria(appendice)] += qta
        s.valore += qta * (prezzo or 0)
    for lunedi, ore in ore_timbrate(settimane).items():
        risultato[lunedi].ore_timbrate = ore["totale"]
    for ol in OreLavorate.objects.filter(settimana__in=settimane):
        risultato[ol.settimana].ore_manuali = ol.ore
    return list(risultato.values())


def produttivita_giornaliera(lunedi: dt.date, committenti: Committenti = None) -> list[dict[str, Any]]:
    giorni = {lunedi + dt.timedelta(days=i): defaultdict(lambda: ZERO) for i in range(7)}
    for data, qta, appendice in _righe_consegnate(lunedi, lunedi + dt.timedelta(days=6), committenti).values_list(
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
def materiale_per_reparto(
    settimane: list[dt.date], committenti: Committenti = None
) -> tuple[list[dict[str, Any]], list[dt.date]]:
    """Per reparto e settimana: ore 'a scadenza' (righe con scadenza nella settimana) e ore 'consegnate'
    (righe consegnate nella settimana). Righe annullate/sostituite escluse."""
    dal, al = settimane[0], settimane[-1] + dt.timedelta(days=6)
    reparto_di = dict(Articolo.objects.values_list("codice", "reparto__nome"))
    dati: dict[str, dict[dt.date, dict[str, Decimal]]] = defaultdict(
        lambda: {s: {"scadenza": ZERO, "consegnato": ZERO} for s in settimane}
    )
    timbrate = ore_timbrate(settimane)
    for ore in timbrate.values():  # anche i reparti dove si è timbrato senza materiale nel periodo
        for nome in ore["per_reparto"]:
            _ = dati[nome]  # crea la riga del reparto
    righe = _righe(committenti).filter(stato=StatoRiga.ATTIVA).exclude(prezzo_unitario=None)
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
        celle = [
            {
                "scadenza": _ore(v["scadenza"]),
                "consegnato": _ore(v["consegnato"]),
                "timbrate": timbrate[lunedi]["per_reparto"].get(nome),
            }
            for lunedi, v in dati[nome].items()
        ]
        righe_out.append(
            {
                "reparto": nome,
                "celle": celle,
                "tot_timbrate": _somma(c["timbrate"] for c in celle),
                "tot_scadenza": _ore(sum((v["scadenza"] for v in dati[nome].values()), ZERO)),
                "tot_consegnato": _ore(sum((v["consegnato"] for v in dati[nome].values()), ZERO)),
            }
        )
    return righe_out, settimane


def evaso_consegnato_per_settimana(settimane: list[dt.date], committenti: Committenti = None) -> dict[str, Any]:
    """Una riga per settimana (dalla più recente) e una colonna per reparto: ore in scadenza e ore consegnate.

    Stessi dati di ``materiale_per_reparto``, girati: ore = valore / resa oraria."""
    per_reparto, _ = materiale_per_reparto(settimane, committenti)
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
                "tot_timbrate": _somma(c["timbrate"] for c in celle),
            }
        )
    totali = [
        {"scadenza": r["tot_scadenza"], "consegnato": r["tot_consegnato"], "timbrate": r["tot_timbrate"]}
        for r in per_reparto
    ]
    return {
        "reparti": reparti,
        "righe": righe,
        "totali": totali,
        "totale_scadenza": _somma(t["scadenza"] for t in totali),
        "totale_consegnato": _somma(t["consegnato"] for t in totali),
        "totale_timbrate": _somma(t["timbrate"] for t in totali),
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
    committenti: list[int] | None = None


def controllo_prezzi(f: FiltriPrezzi) -> QuerySet[RigaOrdine]:
    """Confronto tra il prezzo della riga e il prezzo di riferimento in anagrafica.

    - bolle: righe consegnate nel periodo (data di consegna), con il numero di DDT se registrato;
    - lavorazione: righe non ancora in bolla di ordini aperti (Inserito, Lavorato, In spedizione, Parziale),
      per accorgersi di un prezzo sbagliato PRIMA che vada in bolla (in Metodo era un report separato)."""
    rif = Articolo.objects.filter(codice=OuterRef("articolo")).values("prezzo_riferimento")[:1]
    qs = _righe(f.committenti).filter(stato=StatoRiga.ATTIVA).select_related("ordine")
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


# ================================================================== controllo della produttività (settimanale)
def _soglia(valore: Decimal | None, buono: int, discreto: int) -> str:
    """Classe di colore per percentuali: verde sopra «buono», giallo sopra «discreto», altrimenti rosso."""
    if valore is None:
        return ""
    return "ok" if valore >= buono else "medio" if valore >= discreto else "basso"


def _percentuale(parte: Decimal | None, totale: Decimal | None) -> Decimal | None:
    if parte is None or not totale:
        return None
    return (parte / totale * 100).quantize(Decimal("1"))


def _turni_per_settimana(settimane: list[dt.date]) -> tuple[list[Any], dict[int, dict[dt.date, list[Any]]]]:
    """Turni delle timbrature nel periodo, raggruppati per lavoratore e settimana (lunedì del giorno del turno)."""
    from metodo.timbrature import servizi as timbrature
    from metodo.timbrature.models import Lavoratore

    lavoratori = list(Lavoratore.objects.order_by("cognome", "nome"))
    per_lav: dict[int, dict[dt.date, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for lav_id, tt in timbrature.turni(settimane[0], settimane[-1] + dt.timedelta(days=6), lavoratori).items():
        for t in tt:
            per_lav[lav_id][calendario.lunedi(t.giorno)].append(t)
    return lavoratori, per_lav


def _ore_da_secondi(secondi: float) -> Decimal:
    return (Decimal(str(secondi)) / 3600).quantize(Decimal("0.01"))


def ore_timbrate(settimane: list[dt.date]) -> dict[dt.date, dict[str, Any]]:
    """Per settimana, dalle timbrature: ore lavorate (presenza meno mensa e pausa) in totale e per reparto.

    {lunedì: {"totale": ore | None, "per_reparto": {nome reparto: ore}, "fuori_reparto": ore | None}}. È lo stesso
    calcolo dei turni della sezione Timbrature, usato da tutti i report che confrontano ore timbrate e prodotte."""
    from metodo.core.models import Reparto

    nomi = dict(Reparto.objects.values_list("pk", "nome"))
    sec: dict[dt.date, dict[str, Any]] = {
        s: {"totale": 0.0, "per_reparto": defaultdict(float), "fuori_reparto": 0.0} for s in settimane
    }
    _, per_lav = _turni_per_settimana(settimane)
    for settimane_lav in per_lav.values():
        for lunedi, turni in settimane_lav.items():
            if lunedi not in sec:
                continue
            for t in turni:
                sec[lunedi]["totale"] += t.lavorato
                sec[lunedi]["fuori_reparto"] += t.non_assegnato
                for rep_id, secondi in t.per_reparto.items():
                    sec[lunedi]["per_reparto"][nomi.get(rep_id, "?")] += secondi
    return {
        lunedi: {
            "totale": _ore_da_secondi(v["totale"]) or None,
            "per_reparto": {n: _ore_da_secondi(x) for n, x in v["per_reparto"].items() if x},
            "fuori_reparto": _ore_da_secondi(v["fuori_reparto"]) or None,
        }
        for lunedi, v in sec.items()
    }


def efficienza_reparti(settimane: list[dt.date], con_costi: bool = False) -> dict[str, Any]:
    """Per settimana e reparto: ore timbrate (Timbrature) contro ore prodotte (valore consegnato / resa oraria).

    Efficienza = ore prodotte / ore timbrate. Le ore timbrate fuori da ogni reparto e le righe consegnate di
    articoli senza reparto contano solo nei totali. Con ``con_costi`` anche costo della manodopera e costo per
    ora prodotta (dal costo orario dei lavoratori)."""
    from metodo.core.models import Reparto

    nomi = dict(Reparto.objects.values_list("pk", "nome"))
    lavoratori, per_lav = _turni_per_settimana(settimane)
    costo = {lav.pk: lav.costo_orario for lav in lavoratori}
    sec: dict[dt.date, dict[str | None, float]] = {s: defaultdict(float) for s in settimane}
    euro: dict[dt.date, Decimal] = defaultdict(lambda: ZERO)
    senza_costo = False
    for lav_id, settimane_lav in per_lav.items():
        for lun, tt in settimane_lav.items():
            for t in tt:
                for rep_id, s in [*t.per_reparto.items(), (None, t.non_assegnato)]:
                    sec[lun][nomi.get(rep_id) if rep_id else None] += s
                    if costo[lav_id] is None:
                        senza_costo = senza_costo or s > 0
                    else:
                        euro[lun] += Decimal(str(s)) / 3600 * costo[lav_id]
    prodotte_reparto, _ = materiale_per_reparto(settimane)
    prodotte: dict[dt.date, dict[str, Decimal | None]] = {s: {} for s in settimane}
    for r in prodotte_reparto:
        for i, s in enumerate(settimane):
            prodotte[s][r["reparto"]] = r["celle"][i]["consegnato"]
    reparti = sorted(
        {n for s in settimane for n in sec[s] if n} | {r["reparto"] for r in prodotte_reparto} - {"Senza reparto"}
    )
    righe = []
    for lun in reversed(settimane):
        celle = []
        for nome in reparti:
            h = _ore_da_secondi(sec[lun].get(nome, 0))
            p = prodotte[lun].get(nome)
            e = _percentuale(p, h)
            celle.append({"timbrate": h or None, "prodotte": p, "efficienza": e, "classe": _soglia(e, 95, 80)})
        tot_h = _ore_da_secondi(sum(sec[lun].values()))
        tot_p = _somma(prodotte[lun].values())
        e = _percentuale(tot_p, tot_h)
        riga = {
            "lunedi": lun,
            "domenica": lun + dt.timedelta(days=6),
            "celle": celle,
            "timbrate": tot_h or None,
            "non_assegnate": _ore_da_secondi(sec[lun].get(None, 0)) or None,
            "prodotte": tot_p,
            "efficienza": e,
            "classe": _soglia(e, 95, 80),
        }
        if con_costi:
            c = euro[lun].quantize(Decimal("0.01"))
            riga["costo"] = c or None
            riga["costo_ora_prodotta"] = (c / tot_p).quantize(Decimal("0.01")) if c and tot_p else None
        righe.append(riga)
    return {
        "reparti": reparti,
        "righe": righe,
        "con_costi": con_costi,
        "senza_costo": senza_costo,
        "resa_configurata": bool(config.leggi("produzione.prezzo_resa_oraria")),
        "con_timbrature": any(r["timbrate"] for r in righe),
    }


def puntualita_arretrato(settimane: list[dt.date], committenti: Committenti = None) -> list[dict[str, Any]]:
    """Per settimana: righe consegnate entro la scadenza o in ritardo (con il ritardo medio in giorni) e
    arretrato a fine settimana (righe già scadute e non ancora consegnate a quella data)."""
    from django.db.models import DecimalField, ExpressionWrapper

    valore = ExpressionWrapper(
        F("qta") * F("prezzo_unitario"), output_field=DecimalField(max_digits=20, decimal_places=7)
    )
    resa = config.leggi("produzione.prezzo_resa_oraria")
    oggi = timezone.localdate()
    righe = []
    for lun in reversed(settimane):
        dom = lun + dt.timedelta(days=6)
        puntuali = ritardo = 0
        giorni_ritardo = 0
        for consegna, scadenza in (
            _righe(committenti)
            .filter(bolla_emessa=True, data_consegna__range=(lun, dom))
            .exclude(stato=StatoRiga.ANNULLATA)
            .values_list("data_consegna", "data_scadenza")
        ):
            if scadenza is None or consegna <= scadenza:
                puntuali += 1
            else:
                ritardo += 1
                giorni_ritardo += (consegna - scadenza).days
        consegnate = puntuali + ritardo
        # Arretrato «a fine settimana»: per la settimana in corso, ad oggi (ciò che scade oggi non è ancora in ritardo).
        riferimento = min(dom + dt.timedelta(days=1), oggi)
        fine = (
            timezone.now() if riferimento >= oggi else timezone.make_aware(dt.datetime.combine(riferimento, dt.time()))
        )
        arretrato = (
            _righe(committenti)
            .filter(stato=StatoRiga.ATTIVA, data_scadenza__lt=riferimento, creato_il__lt=fine)
            .filter(Q(bolla_emessa=False) | Q(data_consegna__gte=riferimento))
            .aggregate(n=Count("pk"), valore=Sum(valore))
        )
        perc = _percentuale(Decimal(puntuali), Decimal(consegnate)) if consegnate else None
        righe.append(
            {
                "lunedi": lun,
                "domenica": dom,
                "consegnate": consegnate,
                "puntuali": puntuali,
                "in_ritardo": ritardo,
                "puntualita": perc,
                "classe": _soglia(perc, 95, 85),
                "ritardo_medio": (Decimal(giorni_ritardo) / ritardo).quantize(Decimal("0.1")) if ritardo else None,
                "arretrato_righe": arretrato["n"],
                "arretrato_ore": (
                    (arretrato["valore"] / resa).quantize(Decimal("0.01")) if resa and arretrato["valore"] else None
                ),
            }
        )
    return righe


def ore_per_lavoratore(settimane: list[dt.date]) -> dict[str, Any]:
    """Per lavoratore e settimana: ore lavorate (presenza meno mensa e pausa) e quota passata in un reparto.

    «In reparto» basso significa ore di presenza non attribuite a nessun reparto: tempo che non si sa dove
    sia andato (o timbrature di reparto dimenticate)."""
    lavoratori, per_lav = _turni_per_settimana(settimane)
    righe = []
    for lav in lavoratori:
        celle = []
        tot_lav = tot_rep = ZERO
        anomalie = 0
        for lun in reversed(settimane):
            tt = per_lav.get(lav.pk, {}).get(lun, [])
            lavorato = _ore_da_secondi(sum(t.lavorato for t in tt))
            in_reparto = _ore_da_secondi(sum(sum(t.per_reparto.values()) for t in tt))
            anomalie += sum(len(t.anomalie) for t in tt)
            tot_lav += lavorato
            tot_rep += in_reparto
            q = _percentuale(in_reparto, lavorato)
            celle.append({"ore": lavorato or None, "in_reparto": q, "classe": _soglia(q, 90, 75), "turni": len(tt)})
        if not tot_lav and not lav.attivo:
            continue
        q = _percentuale(tot_rep, tot_lav)
        righe.append(
            {
                "lavoratore": lav,
                "celle": celle,
                "totale": tot_lav or None,
                "in_reparto": q,
                "classe": _soglia(q, 90, 75),
                "media": (tot_lav / len(settimane)).quantize(Decimal("0.1")) if tot_lav else None,
                "anomalie": anomalie,
            }
        )
    return {"settimane": list(reversed(settimane)), "righe": righe}
