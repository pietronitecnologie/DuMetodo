"""Aggiornamenti del client desktop: l'amministratore carica il nuovo eseguibile, i client lo propongono."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from .models import VersioneClient, numeri_versione

MASSIMO_BYTE = 80 * 1024 * 1024
_RE_VERSIONE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")


class AggiornamentoNonValido(ValueError):
    pass


def ultima() -> VersioneClient | None:
    """La versione più alta tra quelle proposte ai client."""
    versioni = list(VersioneClient.objects.filter(attiva=True))
    return max(versioni, key=lambda v: v.numeri, default=None)


@transaction.atomic
def carica(utente: Any, versione: str, file: UploadedFile, note: str = "") -> VersioneClient:
    from dumetodo.core.permessi import F, ha_permesso

    if not ha_permesso(utente, F.CONFIGURAZIONE):
        raise AggiornamentoNonValido("Non hai i permessi per caricare aggiornamenti del client.")
    versione = (versione or "").strip().lstrip("vV")
    if not _RE_VERSIONE.match(versione):
        raise AggiornamentoNonValido("Versione non valida: usa il formato 0.3.0 (come in client/Cargo.toml).")
    if VersioneClient.objects.filter(versione=versione).exists():
        raise AggiornamentoNonValido(f"La versione {versione} è già stata caricata.")
    if file is None or not file.size:
        raise AggiornamentoNonValido("Scegli il file dumetodo-client.exe.")
    if file.size > MASSIMO_BYTE:
        raise AggiornamentoNonValido("File troppo grande (massimo 80 MB).")
    impronta = hashlib.sha256()
    inizio = b""
    for blocco in file.chunks():
        if not inizio:
            inizio = blocco[:2]
        impronta.update(blocco)
    if inizio != b"MZ":
        raise AggiornamentoNonValido("Il file non è un programma Windows (.exe).")
    file.seek(0)
    return VersioneClient.objects.create(
        versione=versione,
        file=file,
        sha256=impronta.hexdigest(),
        dimensione=file.size,
        note=(note or "").strip(),
        caricata_da=utente,
    )


def piu_recente_di(versione_client: str) -> VersioneClient | None:
    v = ultima()
    if v is None or v.numeri <= numeri_versione(versione_client):
        return None
    return v
