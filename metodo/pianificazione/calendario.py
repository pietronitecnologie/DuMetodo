from __future__ import annotations

import datetime as dt
from decimal import Decimal


def lunedi(giorno: dt.date) -> dt.date:
    return giorno - dt.timedelta(days=giorno.weekday())


def settimana(giorno: dt.date) -> tuple[dt.date, dt.date]:
    inizio = lunedi(giorno)
    return inizio, inizio + dt.timedelta(days=6)


def ore_rimanenti_settimana(adesso: dt.datetime, ora_inizio: int, ora_fine: int) -> Decimal:
    """Ore lavorative rimaste da ``adesso`` a venerdì sera (lun-ven, orario fisso), come in Metodo."""
    ore_giorno = Decimal(max(ora_fine - ora_inizio, 0))
    giorno = adesso.weekday()
    if giorno >= 5:
        return Decimal(0)
    ora = Decimal(adesso.hour) + Decimal(adesso.minute) / 60
    if ora < ora_inizio:
        oggi = ore_giorno
    elif ora >= ora_fine:
        oggi = Decimal(0)
    else:
        oggi = Decimal(ora_fine) - ora
    return (oggi + (4 - giorno) * ore_giorno).quantize(Decimal("0.01"))


def ore_settimana(ora_inizio: int, ora_fine: int) -> Decimal:
    return Decimal(max(ora_fine - ora_inizio, 0) * 5)
