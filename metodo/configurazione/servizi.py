"""API per leggere e scrivere i parametri. Il resto del codice usa SOLO queste funzioni.

from metodo.configurazione import servizi as config
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
from .models import SegretoCommittente, SegretoParametro, ValoreCommittente, ValoreParametro
from .registro import PROPRIO, REGISTRO, Parametro, Tipo

logger = logging.getLogger(__name__)

_CACHE_KEY = "configurazione:valori:v1"
_CACHE_COMMITTENTI = "configurazione:committenti:v1"
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
    cache.delete_many([_CACHE_KEY, _CACHE_COMMITTENTI])


def _valori_committenti() -> dict[str, Any]:
    """{"<id committente>:<chiave>": valore, "predefinito": id del committente predefinito}."""
    valori = cache.get(_CACHE_COMMITTENTI)
    if valori is None:
        from metodo.committenti.models import Committente

        valori = {
            f"{c}:{k}": v for c, k, v in ValoreCommittente.objects.values_list("committente_id", "chiave", "valore")
        }
        valori["predefinito"] = Committente.objects.filter(predefinito=True).values_list("pk", flat=True).first()
        cache.set(_CACHE_COMMITTENTI, valori, _CACHE_TIMEOUT)
    return valori


def _id(committente: Any) -> int | None:
    return getattr(committente, "pk", committente) if committente is not None else None


def ha_valore_proprio(chiave: str, committente: Any) -> bool:
    """Il committente ha un valore suo per il parametro (non eredita quello generale)."""
    cid, p = _id(committente), _parametro(chiave)
    if p.tipo == Tipo.SEGRETO:
        return SegretoCommittente.objects.filter(committente_id=cid, chiave=chiave).exists()
    return f"{cid}:{chiave}" in _valori_committenti()


def eredita_generale(chiave: str, committente: Any) -> bool:
    """Senza un valore suo, il committente usa quello generale? (No per i parametri PROPRI dei non predefiniti.)"""
    p = _parametro(chiave)
    return p.per_committente != PROPRIO or _id(committente) == _valori_committenti()["predefinito"]


def leggi(chiave: str, committente: Any = None) -> Any:
    """Valore tipizzato del parametro (o il default). Non solleva errori se manca.

    ``committente`` (istanza o id): per i parametri per committente prevale il suo valore; se non ce l'ha,
    vale quello generale (EREDITA) oppure, per i parametri PROPRI di un committente non predefinito, il default.
    """
    p = _parametro(chiave)
    cid = _id(committente)
    if cid is not None and p.per_committente:
        if p.tipo == Tipo.SEGRETO:
            sc = SegretoCommittente.objects.filter(committente_id=cid, chiave=chiave).first()
            if sc is not None:
                return segreti.decifra(sc.cifrato)
        else:
            propri = _valori_committenti()
            if f"{cid}:{chiave}" in propri:
                try:
                    return p.da_json(propri[f"{cid}:{chiave}"])
                except ValidationError:
                    logger.warning("Valore non valido per %s del committente %s", chiave, cid)
        if not eredita_generale(chiave, cid):
            return None if p.tipo == Tipo.SEGRETO else p.valore_default()
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
def cancella_valore(chiave: str, utente: Any = None) -> None:
    """Torna al valore predefinito (es. "Rimuovi logo")."""
    ValoreParametro.objects.filter(chiave=_parametro(chiave).chiave).delete()
    transaction.on_commit(invalida_cache)


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
        if _parametro(chiave).tipo == Tipo.IMMAGINE and not valore:
            continue  # nessun file caricato: l'immagine resta quella di prima
        attuale = ValoreParametro.objects.filter(chiave=chiave).first()
        nuovo = _parametro(chiave).a_json(valore)
        if attuale is not None and attuale.valore == nuovo:
            continue
        ValoreParametro.objects.update_or_create(chiave=chiave, defaults={"valore": nuovo, "aggiornato_da": utente})
    transaction.on_commit(invalida_cache)


# ------------------------------------------------------------------------------------------ per committente
@transaction.atomic
def scrivi_committente(committente: Any, valori: Mapping[str, Any], utente: Any = None) -> None:
    """Valori propri di un committente. ``None`` = nessun valore suo (torna a ereditare); per le password
    ``None`` lascia quella salvata (si cancella con ``cancella_segreto_committente``). Tutto o niente."""
    cid = _id(committente)
    errori: dict[str, list[str]] = {}
    convertiti: dict[str, Any] = {}
    for chiave, valore in valori.items():
        p = _parametro(chiave)
        if not p.per_committente:
            raise ValueError(f"{chiave} non si imposta per committente")
        if valore is None:
            convertiti[chiave] = None
            continue
        try:
            convertiti[chiave] = p.converti(valore)
        except ValidationError as exc:
            errori[chiave] = exc.messages
    if errori:
        raise ValidationError(errori)
    for chiave, valore in convertiti.items():
        p = _parametro(chiave)
        if p.tipo == Tipo.SEGRETO:
            if valore:
                SegretoCommittente.objects.update_or_create(
                    committente_id=cid,
                    chiave=chiave,
                    defaults={"cifrato": segreti.cifra(valore), "aggiornato_da": utente},
                )
            continue
        if valore is None:
            ValoreCommittente.objects.filter(committente_id=cid, chiave=chiave).delete()
            continue
        nuovo = p.a_json(valore)
        attuale = ValoreCommittente.objects.filter(committente_id=cid, chiave=chiave).first()
        if attuale is None or attuale.valore != nuovo:
            ValoreCommittente.objects.update_or_create(
                committente_id=cid, chiave=chiave, defaults={"valore": nuovo, "aggiornato_da": utente}
            )
    invalida_cache()  # subito (anche per chi legge nella stessa transazione) e di nuovo al commit
    transaction.on_commit(invalida_cache)


@transaction.atomic
def cancella_segreto_committente(committente: Any, chiave: str, utente: Any = None) -> None:
    SegretoCommittente.objects.filter(committente_id=_id(committente), chiave=chiave).delete()
    logger.info("Parametro segreto %s del committente %s cancellato da %s", chiave, _id(committente), utente)
