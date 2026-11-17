"""Estrazione sicura dei file d'ordine dagli allegati.

In Metodo gli zip venivano salvati con il nome scelto dal mittente ed estratti con
``extractall`` in una cartella condivisa: path traversal, zip-slip e collisioni tra processi.
Qui tutto avviene in memoria, solo i .txt vengono letti, con limiti di dimensione.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

MAX_FILE_NELLO_ZIP = 50


@dataclass
class FileEstratto:
    nome: str
    contenuto: bytes


class AllegatoNonValido(Exception):
    pass


def _nome_sicuro(nome: str) -> str:
    # Toglie qualsiasi percorso (anche in stile Windows) e caratteri di controllo.
    base = PureWindowsPath(PurePosixPath(nome).name).name
    return "".join(ch for ch in base if ch.isprintable()).strip()[:200] or "senza_nome.txt"


def estrai_file_ordine(nome: str, contenuto: bytes, *, max_byte: int) -> list[FileEstratto]:
    nome = _nome_sicuro(nome)
    minuscolo = nome.lower()
    if len(contenuto) > max_byte:
        raise AllegatoNonValido(f"{nome}: allegato troppo grande ({len(contenuto)} byte).")
    if minuscolo.endswith(".txt"):
        return [FileEstratto(nome, contenuto)]
    if not minuscolo.endswith(".zip"):
        return []
    try:
        archivio = zipfile.ZipFile(io.BytesIO(contenuto))
    except zipfile.BadZipFile as exc:
        raise AllegatoNonValido(f"{nome}: archivio zip non valido.") from exc
    estratti: list[FileEstratto] = []
    totale = 0
    with archivio:
        voci = [i for i in archivio.infolist() if not i.is_dir()]
        if len(voci) > MAX_FILE_NELLO_ZIP:
            raise AllegatoNonValido(f"{nome}: troppi file nell'archivio ({len(voci)}).")
        for info in voci:
            interno = _nome_sicuro(info.filename)
            if not interno.lower().endswith(".txt"):
                continue  # i PDF dell'ordine non servono all'importazione
            if info.flag_bits & 0x1:
                raise AllegatoNonValido(f"{nome}: {interno} è protetto da password.")
            if info.file_size > max_byte or totale + info.file_size > max_byte:
                raise AllegatoNonValido(f"{nome}: contenuto estratto troppo grande.")
            with archivio.open(info) as fh:
                dati = fh.read(max_byte + 1)  # non ci si fida della dimensione dichiarata
            if len(dati) > max_byte:
                raise AllegatoNonValido(f"{nome}: contenuto estratto troppo grande.")
            totale += len(dati)
            estratti.append(FileEstratto(f"{nome}/{interno}", dati))
    return estratti
