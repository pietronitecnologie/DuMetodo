"""Timbrature di Metodo → timbrature di Metodo v2.

In Metodo (app Timbratura) una timbratura è l'**arrivo in un luogo** (``Plan.Location``) a un'ora: l'ingresso
(percorso ``GK01-IN``), l'uscita (``GK01-OUT``), la mensa (``GK01-MSN``, area «Mensa») o un reparto; si resta lì
fino alla timbratura successiva. In Metodo v2 le timbrature sono **eventi**: entrata, uscita, inizio reparto,
inizio e fine mensa (vedi timbrature/calcolo.py). La conversione segue il lavoratore da una timbratura alla
successiva:

=================  ====================================================================
arrivo in          eventi in Metodo v2 (tutti all'ora della timbratura)
=================  ====================================================================
ingresso           ENT (se non era già dentro); dentro: fine mensa / fine reparto
reparto            ENT se non era dentro, fine mensa se era in mensa, poi IRP(reparto)
mensa              ENT se non era dentro, poi IME
uscita             fine mensa se era in mensa, poi USC
=================  ====================================================================

«Non era dentro» = nessuna timbratura prima, ultima timbratura un'uscita, o più di 16 ore dall'ultima (turno
chiuso, come nel calcolo di Metodo v2): così una giornata iniziata direttamente in un reparto ha la sua entrata
(stessa regola delle postazioni di Metodo v2).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from metodo.timbrature.calcolo import MASSIMO_TURNO

INGRESSO, USCITA, MENSA, REPARTO = "ingresso", "uscita", "mensa", "reparto"
CODICI = {"GK01-IN": INGRESSO, "GK01-OUT": USCITA, "GK01-MSN": MENSA}


def luogo(codice: str, nome: str = "") -> str:
    """Che cosa rappresenta un luogo di Metodo, dal codice del percorso (o dal nome dell'area)."""
    if codice.upper() in CODICI:
        return CODICI[codice.upper()]
    return MENSA if nome.strip().lower() == "mensa" else REPARTO


@dataclass(frozen=True)
class Stato:
    """Dove si trova il lavoratore dopo l'ultima timbratura convertita."""

    dove: str | None = None  # None = fuori; INGRESSO (dentro, fuori dai reparti), MENSA, REPARTO
    reparto_id: int | None = None
    quando: dt.datetime | None = None


def stato_da_evento(tipo: str, reparto_id: int | None, quando: dt.datetime) -> Stato:
    """Lo stato dopo un evento di Metodo v2 (per riprendere un prelievo dall'ultima timbratura importata)."""
    if tipo == "USC":
        return Stato(None, None, quando)
    if tipo == "IRP":
        return Stato(REPARTO, reparto_id, quando)
    if tipo == "IME":
        return Stato(MENSA, None, quando)
    return Stato(INGRESSO, None, quando)


def converti(
    prima: Stato, arrivo: str, reparto_id: int | None, quando: dt.datetime
) -> tuple[list[tuple[str, int | None]], Stato]:
    """Eventi di Metodo v2 per l'arrivo in un luogo, e il nuovo stato del lavoratore."""
    dentro = prima.dove is not None and prima.quando is not None and quando - prima.quando <= MASSIMO_TURNO
    eventi: list[tuple[str, int | None]] = []
    if not dentro:
        if arrivo == USCITA:  # uscita senza entrata: si registra com'è (il calcolo la segnala come anomalia)
            return [("USC", None)], Stato(None, None, quando)
        eventi.append(("ENT", None))
    elif prima.dove == MENSA and arrivo != MENSA:
        eventi.append(("FME", None))
    if arrivo == USCITA:
        eventi.append(("USC", None))
        return eventi, Stato(None, None, quando)
    if arrivo == MENSA:
        if not (dentro and prima.dove == MENSA):
            eventi.append(("IME", None))
        return eventi, Stato(MENSA, None, quando)
    if arrivo == REPARTO and reparto_id is not None:
        if not (dentro and prima.dove == REPARTO and prima.reparto_id == reparto_id):
            eventi.append(("IRP", reparto_id))
        return eventi, Stato(REPARTO, reparto_id, quando)
    # ingresso (o luogo sconosciuto): dentro l'azienda ma fuori dai reparti
    if dentro and prima.dove in (REPARTO, MENSA):  # dopo la mensa il calcolo riprenderebbe il reparto di prima
        eventi.append(("FRP", None))
    return eventi, Stato(INGRESSO, None, quando)
