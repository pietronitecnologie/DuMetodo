from __future__ import annotations

from collections.abc import Iterable

from django.db.models import OuterRef, Subquery

from dumetodo.configurazione import servizi as config
from dumetodo.core.models import Reparto

from .models import Articolo


def registra_articoli(codici: Iterable[str]) -> int:
    """Crea in anagrafica gli articoli non ancora presenti (idempotente, sicuro in concorrenza)."""
    codici = {c.strip() for c in codici if c and c.strip()}
    if not codici:
        return 0
    esistenti = set(Articolo.objects.filter(codice__in=codici).values_list("codice", flat=True))
    nuovi = codici - esistenti
    if not nuovi:
        return 0
    reparto = Reparto.objects.filter(codice=config.leggi("anagrafica.reparto_nuovi_articoli")).first()
    Articolo.objects.bulk_create(
        [Articolo(codice=c[:50], reparto=reparto) for c in sorted(nuovi)], ignore_conflicts=True
    )
    return len(nuovi)


def sincronizza_da_righe() -> int:
    """Registra tutti gli articoli presenti nelle righe d'ordine (es. dopo la migrazione)."""
    from dumetodo.ordini.models import RigaOrdine

    return registra_articoli(RigaOrdine.objects.values_list("articolo", flat=True).distinct())


def descrizione_subquery() -> Subquery:
    """Da usare in ``.annotate(descrizione=...)`` su un queryset di righe d'ordine."""
    return Subquery(Articolo.objects.filter(codice=OuterRef("articolo")).values("descrizione")[:1])
