"""Conversione dei modelli nei dizionari usati dai tracciati (vedi linguaggio.CAMPI)."""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from metodo.ordini.models import Ordine, RigaOrdine


def documento(utente: Any, selezione: str = "", numero: str = "", data: Any = None) -> dict[str, Any]:
    ora = timezone.localtime()
    return {
        "oggi": ora.date(),
        "ora": ora,
        "selezione": selezione,
        "utente": (utente.get_full_name() or utente.get_username()) if utente else "",
        "numero": numero,
        "data": data or ora.date(),
    }


def ordine(o: Ordine) -> dict[str, Any]:
    return {
        "serie": o.serie,
        "numero": o.numero,
        "appendice": o.appendice,
        "anno": o.anno,
        "codice": o.codice,
        "codice_breve": o.codice_breve,
        "cliente": o.cliente,
        "data_scadenza": o.data_scadenza,
    }


def riga(r: RigaOrdine, qta: Any = None, prezzo: Any = None) -> dict[str, Any]:
    qta = r.qta if qta is None else qta
    prezzo = r.prezzo_unitario if prezzo is None else prezzo
    return {
        "n_riga": r.n_riga,
        "articolo": r.articolo,
        "variante": r.variante,
        "qta": qta,
        "prezzo": prezzo,
        "sconto": r.sconto,
        "importo": None if prezzo is None else (qta * prezzo).quantize(prezzo.__class__("0.01")),
        "cliente": r.cliente,
        "magazzino": r.magazzino,
        "data_scadenza": r.data_scadenza,
    }
