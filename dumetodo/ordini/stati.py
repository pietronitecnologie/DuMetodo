"""Macchina a stati dell'ordine, applicata lato server.

In Metodo ``setOrdine`` accettava qualunque valore ``s`` passato in GET (anche vuoto, causando
errori 500 dopo aver già salvato la riga). Qui ogni transizione è dichiarata con il ruolo minimo.
Le transizioni verso CONSEGNATO passano dal servizio di consegna (genera la bolla); l'unica
eccezione è la "consegna forzata" riservata agli amministratori, come in Metodo.
"""

from __future__ import annotations

from dataclasses import dataclass

from dumetodo.core.ruoli import Ruolo

from .models import StatoOrdine as S


@dataclass(frozen=True)
class Transizione:
    da: frozenset[str]
    a: str
    ruolo: Ruolo
    etichetta: str


_APERTI = frozenset({S.INSERITO, S.IN_LAVORAZIONE, S.IN_SPEDIZIONE, S.EVASIONE_PARZIALE})

TRANSIZIONI: tuple[Transizione, ...] = (
    Transizione(
        frozenset({S.INSERITO, S.IN_SPEDIZIONE, S.EVASIONE_PARZIALE}),
        S.IN_LAVORAZIONE,
        Ruolo.OPERATORE,
        "In lavorazione",
    ),
    Transizione(frozenset({S.CONSEGNATO}), S.IN_LAVORAZIONE, Ruolo.AMMINISTRATORE, "Riapri (in lavorazione)"),
    # SPE può diventare SPP se ci sono righe bloccate: lo gestisce servizi.cambia_stato
    Transizione(frozenset({S.INSERITO, S.IN_LAVORAZIONE}), S.IN_SPEDIZIONE, Ruolo.OPERATORE, "Spedisci"),
    Transizione(frozenset({S.IN_SPEDIZIONE, S.EVASIONE_PARZIALE}), S.INSERITO, Ruolo.OPERATORE, "Rimuovi da consegna"),
    Transizione(_APERTI, S.CONSEGNATO, Ruolo.AMMINISTRATORE, "Consegna forzata (senza bolla)"),
    Transizione(_APERTI, S.ANNULLATO, Ruolo.AMMINISTRATORE, "Annulla"),
    Transizione(frozenset({S.ANNULLATO}), S.INSERITO, Ruolo.AMMINISTRATORE, "Ripristina"),
)


def transizione(da: str, a: str) -> Transizione | None:
    return next((t for t in TRANSIZIONI if t.a == a and da in t.da), None)


def transizioni_possibili(da: str) -> list[Transizione]:
    return [t for t in TRANSIZIONI if da in t.da]
