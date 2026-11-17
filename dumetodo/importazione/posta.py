"""Lettura della casella IMAP degli ordini.

- una sola esecuzione alla volta (advisory lock PostgreSQL), anche con più worker;
- cursore per UID con UIDVALIDITY; casella aperta in sola lettura (nessuna email cancellata);
- ogni messaggio è registrato (anche quelli senza allegati, che Metodo rileggeva all'infinito);
- deduplica per Message-ID, così uno spostamento di cartella non reimporta nulla;
- il cursore avanza dopo OGNI messaggio: un'interruzione non fa perdere né ripetere lavoro.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from dumetodo.configurazione import servizi as config

from . import servizi
from .allegati import AllegatoNonValido, estrai_file_ordine
from .models import EsitoMessaggio, MessaggioEmail, OrigineFile, StatoCasella

logger = logging.getLogger(__name__)

_LOCK_ID = 0x0D0E7_0D0  # identificativo arbitrario ma fisso dell'advisory lock
MESSAGGI_PER_GIRO = 50


@dataclass
class EsitoLetturaCasella:
    eseguita: bool
    messaggi: int = 0
    file: int = 0
    motivo: str = ""


_LOCK_LOCALE = threading.Lock()


@contextmanager
def _lock_esclusivo() -> Iterator[bool]:
    if connection.vendor != "postgresql":
        # Installazione semplice (SQLite): un solo processo, basta un lock locale.
        ottenuto = _LOCK_LOCALE.acquire(blocking=False)
        try:
            yield ottenuto
        finally:
            if ottenuto:
                _LOCK_LOCALE.release()
        return
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [_LOCK_ID])
        ottenuto = bool(cur.fetchone()[0])
    try:
        yield ottenuto
    finally:
        if ottenuto:
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [_LOCK_ID])


def casella_configurata() -> bool:
    c = settings.ORDINI_IMAP
    return bool(c["HOST"] and c["USER"] and c["PASSWORD"])


def _client() -> Any:
    from imapclient import IMAPClient

    c = settings.ORDINI_IMAP
    client = IMAPClient(c["HOST"], port=c["PORT"], ssl=c["SSL"], timeout=60)
    client.login(c["USER"], c["PASSWORD"])
    return client


def leggi_casella(*, client_factory: Any = None) -> EsitoLetturaCasella:
    if not config.leggi("importazione.lettura_mail_attiva"):
        return EsitoLetturaCasella(False, motivo="lettura automatica disattivata")
    if not casella_configurata():
        return EsitoLetturaCasella(False, motivo="casella IMAP non configurata")
    with _lock_esclusivo() as ottenuto:
        if not ottenuto:
            return EsitoLetturaCasella(False, motivo="lettura già in corso")
        return _leggi(client_factory or _client)


def _leggi(client_factory: Any) -> EsitoLetturaCasella:
    c = settings.ORDINI_IMAP
    nome_casella = f"{c['USER']}@{c['HOST']}/{c['FOLDER']}"
    stato, _ = StatoCasella.objects.get_or_create(casella=nome_casella)
    esito = EsitoLetturaCasella(True)
    client = client_factory()
    try:
        info = client.select_folder(c["FOLDER"], readonly=True)
        uidvalidity = int(info[b"UIDVALIDITY"])
        if stato.uidvalidity != uidvalidity:
            logger.warning("UIDVALIDITY cambiata per %s: rilettura completa (deduplica per Message-ID)", nome_casella)
            stato.uidvalidity, stato.ultimo_uid = uidvalidity, 0
            stato.save()
        uids = sorted(u for u in client.search(["UID", f"{stato.ultimo_uid + 1}:*"]) if u > stato.ultimo_uid)
        max_byte = config.leggi("importazione.dimensione_massima_allegato_mb") * 1024 * 1024
        for uid in uids[:MESSAGGI_PER_GIRO]:
            dati = client.fetch([uid], ["RFC822.SIZE", "INTERNALDATE"])[uid]
            if dati[b"RFC822.SIZE"] > max_byte * 2:
                _registra(
                    nome_casella,
                    uidvalidity,
                    uid,
                    None,
                    dati.get(b"INTERNALDATE"),
                    EsitoMessaggio.SCARTATO,
                    "messaggio troppo grande",
                )
            else:
                corpo = client.fetch([uid], ["BODY.PEEK[]"])[uid][b"BODY[]"]
                esito.file += _elabora_messaggio(
                    nome_casella, uidvalidity, uid, corpo, dati.get(b"INTERNALDATE"), max_byte
                )
            esito.messaggi += 1
            stato.ultimo_uid = uid
            stato.save(update_fields=["ultimo_uid"])
        stato.ultima_lettura_il = timezone.now()
        stato.ultimo_errore = ""
        stato.save()
    except Exception as exc:
        stato.ultimo_errore = f"{timezone.now():%d/%m/%Y %H:%M} {type(exc).__name__}: {exc}"[:2000]
        stato.save(update_fields=["ultimo_errore"])
        raise
    finally:
        try:
            client.logout()
        except Exception:
            logger.debug("logout IMAP non riuscito", exc_info=True)
    return esito


def _registra(
    casella: str, uidvalidity: int, uid: int, msg: Any, ricevuto: Any, esito: str, dettaglio: str = ""
) -> MessaggioEmail:
    if ricevuto is not None and timezone.is_naive(ricevuto):
        ricevuto = timezone.make_aware(ricevuto)
    return MessaggioEmail.objects.create(
        casella=casella,
        uidvalidity=uidvalidity,
        uid=uid,
        message_id=(str(msg.get("Message-ID", "")) if msg else "")[:500],
        mittente=(str(msg.get("From", "")) if msg else "")[:500],
        oggetto=(str(msg.get("Subject", "")) if msg else "")[:500],
        ricevuto_il=ricevuto,
        esito=esito,
        dettaglio=dettaglio,
    )


def _elabora_messaggio(casella: str, uidvalidity: int, uid: int, raw: bytes, ricevuto: Any, max_byte: int) -> int:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    message_id = str(msg.get("Message-ID", "")).strip()
    if message_id and MessaggioEmail.objects.filter(message_id=message_id, esito=EsitoMessaggio.ELABORATO).exists():
        _registra(casella, uidvalidity, uid, msg, ricevuto, EsitoMessaggio.DUPLICATO)
        return 0

    estratti, problemi = [], []
    for parte in msg.iter_attachments():
        nome = parte.get_filename()
        if not nome:
            continue
        try:
            estratti += estrai_file_ordine(nome, parte.get_payload(decode=True) or b"", max_byte=max_byte)
        except AllegatoNonValido as exc:
            problemi.append(str(exc))

    with transaction.atomic():
        registrato = _registra(
            casella,
            uidvalidity,
            uid,
            msg,
            ricevuto,
            EsitoMessaggio.ELABORATO
            if estratti
            else (EsitoMessaggio.SCARTATO if problemi else EsitoMessaggio.SENZA_ALLEGATI),
            "\n".join(problemi),
        )
        files = [servizi.registra_file(f.nome, f.contenuto, OrigineFile.EMAIL, messaggio=registrato) for f in estratti]
    for f in files:
        servizi.importa(f)
    return len(files)
