"""Aiuti visivi per gli operatori.

Cambio articolo: nelle righe di un ordine il codice articolo cambia colore ogni volta che
l'articolo è diverso da quello della riga precedente, così l'operatore vede a colpo d'occhio
dove finisce un gruppo di righe dello stesso codice e ne inizia un altro.

Stessa palette di Metodo. Differenza: in Metodo il contatore dei colori era globale al processo
(``nextColor.counter``), quindi lo stesso ordine poteva apparire con colori diversi a ogni
apertura. Qui la colorazione riparte sempre dal primo colore: stesso ordine, stessi colori.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, TypeVar

PALETTE_CAMBIO_ARTICOLO: tuple[str, ...] = ("#89A6FB", "#BA5C12", "#1A535C", "#B48B7D", "#2B0504", "#3E2F5B")


class _ConArticolo(Protocol):
    articolo: str


T = TypeVar("T", bound=_ConArticolo)


def colora_cambi_articolo[T: _ConArticolo](
    righe: Iterable[T], palette: tuple[str, ...] = PALETTE_CAMBIO_ARTICOLO
) -> list[T]:
    """Imposta su ogni riga ``colore`` (colore del testo dell'articolo) e ``cambio_articolo``
    (True sulla prima riga di ogni gruppo). Le righe vanno passate nell'ordine di visualizzazione."""
    risultato: list[T] = []
    indice = -1
    precedente: str | None = None
    for riga in righe:
        cambio = riga.articolo != precedente
        if cambio:
            indice = (indice + 1) % len(palette)
        riga.colore = palette[indice]  # type: ignore[attr-defined]
        riga.cambio_articolo = cambio  # type: ignore[attr-defined]
        precedente = riga.articolo
        risultato.append(riga)
    return risultato
