"""Dalle timbrature di un lavoratore ai turni: presenza, ore per reparto, mensa, pausa, anomalie.

Regole:
* Un turno va dall'ENTRATA all'USCITA (anche a cavallo della mezzanotte) e appartiene al giorno dell'entrata.
* Dentro il turno il tempo va al reparto in cui si è timbrato l'inizio, finché non si timbra la fine, un altro
  reparto, mensa, pausa o l'uscita; il tempo fuori da ogni reparto è «non assegnato».
* Mensa e pausa sospendono il reparto: alla fine il tempo torna al reparto di prima, senza timbrare di nuovo.
* Un turno senza uscita: se è di oggi è «in corso» (conta fino ad adesso), altrimenti si chiude all'ultima
  timbratura e si segnala l'anomalia. Oltre ``MASSIMO_TURNO`` senza timbrature il turno si considera chiuso.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

ENT, USC, IRP, FRP, IME, FME, IPA, FPA = "ENT", "USC", "IRP", "FRP", "IME", "FME", "IPA", "FPA"
MASSIMO_TURNO = dt.timedelta(hours=16)


@dataclass(frozen=True)
class Evento:
    momento: dt.datetime
    tipo: str
    reparto_id: int | None = None


@dataclass
class Turno:
    inizio: dt.datetime
    giorno: dt.date
    fine: dt.datetime | None = None
    in_corso: bool = False
    per_reparto: dict[int, float] = field(default_factory=lambda: defaultdict(float))  # secondi
    non_assegnato: float = 0.0
    mensa: float = 0.0
    pausa: float = 0.0
    anomalie: list[str] = field(default_factory=list)

    @property
    def lavorato(self) -> float:
        """Secondi di lavoro: presenza meno mensa e pausa."""
        return self.non_assegnato + sum(self.per_reparto.values())

    @property
    def presenza(self) -> float:
        return self.lavorato + self.mensa + self.pausa


@dataclass
class Stato:
    """Dove si trova il lavoratore adesso (serve alla postazione per proporre le azioni)."""

    presente: bool = False
    reparto_id: int | None = None
    in_mensa: bool = False
    in_pausa: bool = False
    dal: dt.datetime | None = None


def calcola(eventi: Iterable[Evento], adesso: dt.datetime, giorno_di: object = None) -> tuple[list[Turno], Stato]:
    """Turni (in ordine) e stato attuale. ``giorno_di`` converte un datetime nel giorno locale."""
    giorno = giorno_di or (lambda m: m.date())
    turni: list[Turno] = []
    t: Turno | None = None
    reparto: int | None = None
    sospeso: int | None = None
    mensa = pausa = False
    ultimo: dt.datetime | None = None

    def accumula(fino: dt.datetime) -> None:
        if t is None or ultimo is None or fino <= ultimo:
            return
        sec = (fino - ultimo).total_seconds()
        if mensa:
            t.mensa += sec
        elif pausa:
            t.pausa += sec
        elif reparto is not None:
            t.per_reparto[reparto] += sec
        else:
            t.non_assegnato += sec

    def chiudi(momento: dt.datetime | None, anomalia: str = "") -> None:
        nonlocal t, reparto, sospeso, mensa, pausa
        if t is None:
            return
        t.fine = momento
        if anomalia:
            t.anomalie.append(anomalia)
        turni.append(t)
        t, reparto, sospeso, mensa, pausa = None, None, None, False, False

    def apri(momento: dt.datetime, anomalia: str = "") -> None:
        nonlocal t
        t = Turno(inizio=momento, giorno=giorno(momento))
        if anomalia:
            t.anomalie.append(anomalia)

    for e in sorted(eventi, key=lambda x: x.momento):
        if t is not None and ultimo is not None and e.momento - ultimo > MASSIMO_TURNO:
            chiudi(ultimo, "uscita mancante")
        accumula(e.momento)
        ultimo = e.momento
        match e.tipo:
            case "ENT":
                if t is not None:
                    t.anomalie.append(f"entrata doppia alle {e.momento:%H:%M}")
                else:
                    apri(e.momento)
            case "USC":
                if t is None:
                    apri(e.momento, "uscita senza entrata")
                chiudi(e.momento)
            case _:
                if t is None:
                    apri(e.momento, "entrata mancante")
                if e.tipo == "IRP":
                    reparto, mensa, pausa, sospeso = e.reparto_id, False, False, None
                elif e.tipo == "FRP":
                    reparto = None
                elif e.tipo in ("IME", "IPA"):
                    if not (mensa or pausa):
                        sospeso = reparto
                    reparto = None
                    mensa, pausa = e.tipo == "IME", e.tipo == "IPA"
                elif e.tipo in ("FME", "FPA"):
                    if (e.tipo == "FME" and not mensa) or (e.tipo == "FPA" and not pausa):
                        t.anomalie.append(f"fine {'mensa' if e.tipo == 'FME' else 'pausa'} senza inizio")
                    mensa = pausa = False
                    reparto, sospeso = sospeso, None

    stato = Stato()
    if t is not None:
        if ultimo is not None and adesso - ultimo <= MASSIMO_TURNO:
            # Turno aperto da poco (anche iniziato ieri, es. turno di notte): in corso fino ad adesso.
            accumula(adesso)
            t.in_corso = True
            stato = Stato(True, reparto, mensa, pausa, t.inizio)
            turni.append(t)
        else:
            chiudi(ultimo, "uscita mancante")
    return turni, stato
