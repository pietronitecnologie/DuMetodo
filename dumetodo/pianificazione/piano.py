"""Calcolo del piano di carico per reparto (ex ``Prod/pianificazione.py``).

Ore = Σ(quantità × prezzo) / resa oraria, per le righe attive non ancora in bolla degli ordini
'Inserito' o 'Evasione parziale' (come in Metodo), raggruppate per reparto dell'articolo.

Differenze rispetto a Metodo:
- una sola implementazione (Metodo aveva lo stesso algoritmo copiato due volte);
- le righe di articoli senza reparto non spariscono: finiscono nella riga "Senza reparto";
- le righe senza prezzo sono contate e segnalate invece di essere ignorate in silenzio;
- i reparti uniti nel report (gruppo_report) non contano due volte lo stesso ordine;
- soglie, settimane e orari sono configurabili.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils import timezone

from dumetodo.anagrafica.models import Articolo
from dumetodo.configurazione import servizi as config
from dumetodo.core.models import Reparto
from dumetodo.ordini.models import RigaOrdine, StatoOrdine

from . import calendario

STATI_PIANIFICATI = (StatoOrdine.INSERITO, StatoOrdine.EVASIONE_PARZIALE)
ZERO = Decimal(0)


@dataclass
class Cella:
    ore: Decimal = ZERO
    ordini: set[int] = field(default_factory=set)
    capacita: Decimal | None = None

    @property
    def n_ordini(self) -> int:
        return len(self.ordini)

    @property
    def percentuale(self) -> Decimal | None:
        if not self.capacita:
            return None
        return (self.ore / self.capacita * 100).quantize(Decimal("0.1"))

    def livello(self, attenzione: int) -> str:
        p = self.percentuale
        if p is None:
            return "nd"
        if p > 100:
            return "critico"
        if p > attenzione:
            return "attenzione"
        return "ok"


@dataclass
class RigaPiano:
    nome: str
    reparti: list[Reparto]
    arretrato: Cella = field(default_factory=Cella)
    settimane: list[Cella] = field(default_factory=list)

    @property
    def totale(self) -> Cella:
        """Settimane + arretrato, sulla capacità complessiva del periodo."""
        c = Cella(capacita=sum((s.capacita or ZERO for s in self.settimane), ZERO) or None)
        c.ore = self.arretrato.ore + sum((s.ore for s in self.settimane), ZERO)
        c.ordini = set().union(self.arretrato.ordini, *(s.ordini for s in self.settimane))
        return c

    @property
    def ore_settimane(self) -> Decimal:
        return sum((s.ore for s in self.settimane), ZERO)


@dataclass
class Piano:
    generato_il: dt.datetime
    settimane: list[tuple[dt.date, dt.date]]
    inizio_arretrato: dt.date
    ore_rimanenti: Decimal
    soglia_attenzione: int
    righe: list[RigaPiano]
    righe_senza_prezzo: int = 0
    resa_mancante: bool = False

    @property
    def etichette(self) -> list[str]:
        return [f"Sett. {i + 1} ({a:%d/%m} - {b:%d/%m})" for i, (a, b) in enumerate(self.settimane)]


def calcola(adesso: dt.datetime | None = None) -> Piano:
    adesso = timezone.localtime(adesso)
    oggi = adesso.date()
    n_sett = config.leggi("pianificazione.settimane")
    lun = calendario.lunedi(oggi)
    settimane = [(lun + dt.timedelta(weeks=i), lun + dt.timedelta(weeks=i, days=6)) for i in range(n_sett)]
    inizio_arretrato = lun - dt.timedelta(weeks=config.leggi("pianificazione.settimane_arretrato"))
    ora_inizio, ora_fine = config.leggi("pianificazione.ora_inizio"), config.leggi("pianificazione.ora_fine")
    rimanenti = calendario.ore_rimanenti_settimana(adesso, ora_inizio, ora_fine)
    ore_sett = calendario.ore_settimana(ora_inizio, ora_fine)
    resa = config.leggi("produzione.prezzo_resa_oraria")

    piano = Piano(
        adesso,
        settimane,
        inizio_arretrato,
        rimanenti,
        config.leggi("pianificazione.soglia_attenzione"),
        [],
        resa_mancante=not resa,
    )
    reparti = list(Reparto.objects.filter(attivo=True))
    righe_piano: dict[int | None, RigaPiano] = {}
    for r in reparti:
        rp = RigaPiano(r.nome, [r], settimane=[Cella() for _ in settimane])
        soglia = r.soglia_ore_settimanali
        if soglia and soglia > 0:
            for i, cella in enumerate(rp.settimane):
                cella.capacita = (
                    (soglia * rimanenti / ore_sett).quantize(Decimal("0.01")) if i == 0 and ore_sett else soglia
                )
        righe_piano[r.pk] = rp
    righe_piano[None] = RigaPiano("Senza reparto", [], settimane=[Cella() for _ in settimane])
    if not resa:
        piano.righe = []
        return piano

    reparto_di = dict(Articolo.objects.exclude(reparto=None).values_list("codice", "reparto_id"))
    qs = RigaOrdine.objects.attive().filter(
        bolla_emessa=False,
        ordine__stato__in=STATI_PIANIFICATI,
        data_scadenza__gte=inizio_arretrato,
        data_scadenza__lte=settimane[-1][1],
    )
    if config.leggi("pianificazione.solo_ordini_con_appendice"):
        qs = qs.exclude(ordine__appendice="")
    for articolo, qta, prezzo, scadenza, ordine_id in qs.values_list(
        "articolo", "qta", "prezzo_unitario", "data_scadenza", "ordine_id"
    ):
        if prezzo is None:
            piano.righe_senza_prezzo += 1
            continue
        rp = righe_piano.get(reparto_di.get(articolo)) or righe_piano[None]
        ore = qta * prezzo / resa
        cella = rp.arretrato if scadenza < lun else rp.settimane[(scadenza - lun).days // 7]
        cella.ore += ore
        cella.ordini.add(ordine_id)

    for rp in righe_piano.values():
        for c in [rp.arretrato, *rp.settimane]:
            c.ore = c.ore.quantize(Decimal("0.01"))
    piano.righe = [rp for rp in righe_piano.values() if rp.totale.ore > 0]
    return piano


def raggruppa_per_report(piano: Piano) -> list[RigaPiano]:
    """Unisce i reparti con lo stesso ``gruppo_report`` (ex 'Area Quadristica')."""
    risultato: list[RigaPiano] = []
    per_gruppo: dict[str, RigaPiano] = {}
    for rp in piano.righe:
        gruppo = rp.reparti[0].gruppo_report.strip() if rp.reparti else ""
        if not gruppo:
            risultato.append(rp)
            continue
        if gruppo not in per_gruppo:
            per_gruppo[gruppo] = RigaPiano(gruppo, [], settimane=[Cella(capacita=None) for _ in piano.settimane])
            risultato.append(per_gruppo[gruppo])
        g = per_gruppo[gruppo]
        g.reparti += rp.reparti
        for dst, src in zip([g.arretrato, *g.settimane], [rp.arretrato, *rp.settimane], strict=True):
            dst.ore += src.ore
            dst.ordini |= src.ordini
            if src.capacita:
                dst.capacita = (dst.capacita or ZERO) + src.capacita
    return risultato
