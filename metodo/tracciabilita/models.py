"""La tracciabilità di Metodo non ha più modelli: tracce, controlli qualità e file sono stati convertiti in
Tracciature (migrazioni tracciatura 0002 e 0003). Restano solo le funzioni citate dalle vecchie migrazioni."""

from __future__ import annotations

import uuid
from pathlib import PurePath


def _percorso(cartella: str, nome: str) -> str:
    """Nome di file casuale (mai quello scelto dall'utente) dentro una cartella non pubblica."""
    estensione = PurePath(nome).suffix.lower()[:10]
    return f"{cartella}/{uuid.uuid4().hex}{estensione}"


def percorso_file_ordine(instance: object, nome: str) -> str:
    """Conservata perché citata dalle migrazioni: foto e file ora sono campi delle Tracciature."""
    return _percorso(f"ordini/{instance.ordine_id}", nome)


def percorso_file_reclamo(instance: object, nome: str) -> str:
    """Conservata solo perché citata dalla migrazione 0001: i reclami sono stati eliminati."""
    return _percorso("reclami", nome)
