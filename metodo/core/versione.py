"""Versione del codice in esecuzione (commit git), mostrata in fondo alle pagine.

Serve a capire al volo se un server è aggiornato. Legge i file di ``.git`` senza usare il comando git;
nell'immagine Docker (senza ``.git``) usa la variabile d'ambiente ``METODO_VERSIONE``.
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path

from django.conf import settings


@cache
def versione() -> str:
    if v := os.environ.get("METODO_VERSIONE", "").strip():
        return v[:40]
    git = Path(settings.BASE_DIR) / ".git"
    try:
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head[:7]
        ref = head[5:]
        file_ref = git / ref
        if file_ref.is_file():
            return file_ref.read_text().strip()[:7]
        for riga in (git / "packed-refs").read_text().splitlines():
            if riga.endswith(" " + ref):
                return riga[:7]
    except OSError:
        pass
    return "sconosciuta"
