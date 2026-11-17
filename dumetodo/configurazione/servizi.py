"""API per leggere e scrivere i parametri. Il resto del codice usa SOLO queste funzioni.

from dumetodo.configurazione import servizi as config
resa = config.leggi("produzione.prezzo_resa_oraria")
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction

from . import segreti
from .models import SegretoParametro, ValoreParametro
from .registro import REGISTRO, Parametro, Tipo

logger = logging.getLogger(__name__)

_CACHE_KEY = "configurazione:valori:v1"
_CACHE_TIMEOUT = 300


class ParametroNonConfigurato(Exception):
    """Un parametro obbligatorio per un'operazione non è stato impostato."""

    def __init__(self, parametro: Parametro) -> None:
        self.parametro = parametro
        super().__init__(
            f"Il parametro '{parametro.etichetta}' ({parametro.chiave}) non è configurato. "
            "Un amministratore deve impostarlo nella pagina Configurazione."
        )


def _parametro(chiave: str) -> Parametro:
    try:
        return REGISTRO[chiave]
    except KeyError:
        raise KeyError(f"Parametro sconosciuto: {chiave!r}. Va dichiarato in configurazione/registro.py") from None


def _valori_grezzi() -> dict[str, Any]:
    valori = cache.get(_CACHE_KEY)
    if valori is None:
        valori = dict(ValoreParametro.objects.values_list("chiave", "valore"))
        cache.set(_CACHE_KEY, valori, _CACHE_TIMEOUT)
    return valori


def invalida_cache() -> None:
    cache.delete(_CACHE_KEY)


def leggi(chiave: str) -> Any:
    """Valore tipizzato del parametro (o il default). Non solleva errori se manca."""
    p = _parametro(chiave)
    if p.tipo == Tipo.SEGRETO:
        # Mai in cache (che può stare su Redis): si legge e decifra al momento.
        s = SegretoParametro.objects.filter(chiave=chiave).first()
        return segreti.decifra(s.cifrato) if s else None
    grezzi = _valori_grezzi()
    if chiave in grezzi:
        try:
            return p.da_json(grezzi[chiave])
        except ValidationError:
            # Valore salvato non più valido (es. regole cambiate): si torna al default, mai un 500.
            logger.warning("Valore non valido per il parametro %s: uso il default", chiave)
    return p.valore_default()


def richiedi(chiave: str) -> Any:
    """Come ``leggi`` ma solleva ``ParametroNonConfigurato`` se il valore è vuoto."""
    valore = leggi(chiave)
    if valore is None or valore == [] or valore == "":
        raise ParametroNonConfigurato(_parametro(chiave))
    return valore


def segreto_impostato(chiave: str) -> bool:
    return SegretoParametro.objects.filter(chiave=chiave).exists()


def _salva_segreto(chiave: str, valore: str | None, utente: Any) -> None:
    """Vuoto = lascia la password com'è (il campo non viene mai precompilato)."""
    if valore:
        SegretoParametro.objects.update_or_create(
            chiave=chiave, defaults={"cifrato": segreti.cifra(valore), "aggiornato_da": utente}
        )


@transaction.atomic
def cancella_segreto(chiave: str, utente: Any = None) -> None:
    if _parametro(chiave).tipo != Tipo.SEGRETO:
        raise ValueError(f"{chiave} non è un parametro segreto")
    SegretoParametro.objects.filter(chiave=chiave).delete()
    logger.info("Parametro segreto %s cancellato da %s", chiave, utente)


@transaction.atomic
def scrivi(chiave: str, valore: Any, utente: Any = None) -> Any:
    p = _parametro(chiave)
    convertito = p.converti(valore)
    if p.tipo == Tipo.SEGRETO:
        _salva_segreto(chiave, convertito, utente)
        return convertito
    ValoreParametro.objects.update_or_create(
        chiave=chiave, defaults={"valore": p.a_json(convertito), "aggiornato_da": utente}
    )
    transaction.on_commit(invalida_cache)
    return convertito


@transaction.atomic
def scrivi_molti(valori: Mapping[str, Any], utente: Any = None) -> None:
    """Valida TUTTI i valori prima di salvarne uno: o si salvano tutti o nessuno."""
    errori: dict[str, list[str]] = {}
    convertiti = {}
    for chiave, valore in valori.items():
        try:
            convertiti[chiave] = _parametro(chiave).converti(valore)
        except ValidationError as exc:
            errori[chiave] = exc.messages
    if errori:
        raise ValidationError(errori)
    for chiave, valore in convertiti.items():
        if _parametro(chiave).tipo == Tipo.SEGRETO:
            _salva_segreto(chiave, valore, utente)
            continue
        attuale = ValoreParametro.objects.filter(chiave=chiave).first()
        nuovo = _parametro(chiave).a_json(valore)
        if attuale is not None and attuale.valore == nuovo:
            continue
        ValoreParametro.objects.update_or_create(chiave=chiave, defaults={"valore": nuovo, "aggiornato_da": utente})
    transaction.on_commit(invalida_cache)
