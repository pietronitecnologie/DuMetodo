"""Lettura della casella IMAP degli ordini.

- una sola esecuzione alla volta (advisory lock PostgreSQL), anche con più worker;
- cursore per UID con UIDVALIDITY; casella aperta in sola lettura (nessuna email cancellata);
- ogni messaggio è registrato (anche quelli senza allegati, che Metodo rileggeva all'infinito);
- deduplica per Message-ID, così uno spostamento di cartella non reimporta nulla;
- il cursore avanza dopo OGNI messaggio: un'interruzione non fa perdere né ripetere lavoro.
"""

from __future__ import annotations

import contextlib
import datetime as dt
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
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from metodo.configurazione import servizi as config

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


def parametri_casella(modulo: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dati della casella ordini: quelli di Configurazione se il server è impostato, altrimenti il file .env.

    ``modulo``: valori non ancora salvati (pulsante "Prova connessione"); password vuota = quella salvata.
    """
    m = modulo or {}

    def valore(chiave: str) -> Any:
        if f"casella.{chiave}" in m or (chiave == "ssl" and modulo is not None):
            p = config.REGISTRO[f"casella.{chiave}"]
            try:
                return p.converti(m.get(f"casella.{chiave}"))
            except ValidationError:
                return config.leggi(f"casella.{chiave}")
        return config.leggi(f"casella.{chiave}")

    server = (valore("server") or "").strip()
    if not server:
        return {**settings.ORDINI_IMAP, "MITTENTI": config.leggi("casella.mittenti_ammessi"), "ORIGINE": ".env"}
    return {
        "HOST": server,
        "PORT": valore("porta") or 993,
        "SSL": bool(valore("ssl")),
        "USER": (valore("utente") or "").strip(),
        "PASSWORD": (m.get("casella.password") or "") or config.leggi("casella.password") or "",
        "FOLDER": (valore("cartella") or "INBOX").strip(),
        "MITTENTI": valore("mittenti_ammessi") or [],
        "ORIGINE": "Configurazione",
    }


def casella_configurata() -> bool:
    c = parametri_casella()
    return bool(c["HOST"] and c["USER"] and c["PASSWORD"])


def _client(c: dict[str, Any] | None = None) -> Any:
    from imapclient import IMAPClient

    c = c or parametri_casella()
    client = IMAPClient(c["HOST"], port=c["PORT"], ssl=c["SSL"], timeout=30)
    client.login(c["USER"], c["PASSWORD"])
    return client


@dataclass
class EsitoProva:
    ok: bool
    messaggio: str


def prova_connessione(c: dict[str, Any], *, client_factory: Any = None) -> EsitoProva:
    """Accede alla casella e apre la cartella, senza leggere né modificare i messaggi."""
    mancanti = [n for n, k in (("server", "HOST"), ("utente", "USER"), ("password", "PASSWORD")) if not c.get(k)]
    if mancanti:
        return EsitoProva(False, f"Mancano: {', '.join(mancanti)}.")
    try:
        client = (client_factory or _client)(c)
    except Exception as exc:
        return EsitoProva(False, f"Accesso non riuscito a {c['HOST']}:{c['PORT']}: {type(exc).__name__}: {exc}"[:500])
    try:
        info = client.select_folder(c["FOLDER"], readonly=True)
        n = int(info.get(b"EXISTS", 0))
        return EsitoProva(True, f"Connessione riuscita: cartella {c['FOLDER']} con {n} messaggi.")
    except Exception as exc:
        return EsitoProva(False, f"Accesso riuscito, ma la cartella {c['FOLDER']!r} non si apre: {exc}"[:500])
    finally:
        with contextlib.suppress(Exception):  # chiusura best effort
            client.logout()


def leggi_casella(*, client_factory: Any = None) -> EsitoLetturaCasella:
    if not config.leggi("importazione.lettura_mail_attiva"):
        return EsitoLetturaCasella(False, motivo="lettura automatica disattivata")
    if not casella_configurata():
        return EsitoLetturaCasella(False, motivo="casella IMAP non configurata")
    with _lock_esclusivo() as ottenuto:
        if not ottenuto:
            return EsitoLetturaCasella(False, motivo="lettura già in corso")
        return _leggi(client_factory or _client)


def _quando(valore: Any) -> dt.datetime | None:
    """INTERNALDATE di IMAP (datetime, a volte senza fuso) → datetime con fuso."""
    if not isinstance(valore, dt.datetime):
        return None
    return timezone.make_aware(valore) if timezone.is_naive(valore) else valore


def _nuovi(client: Any, dal: dt.datetime) -> list[tuple[dt.datetime, int]]:
    """(arrivo, UID) dei messaggi arrivati da «dal» in poi, dal più vecchio.

    IMAP cerca per giorno (SINCE): si chiede dal giorno prima, per i fusi orari, e si filtra all'istante."""
    giorno = (timezone.localtime(dal) - dt.timedelta(days=1)).date()
    uids = client.search(["SINCE", giorno])
    if not uids:
        return []
    dati = client.fetch(uids, ["INTERNALDATE"])
    out = []
    for uid in uids:
        arrivo = _quando(dati.get(uid, {}).get(b"INTERNALDATE"))
        if arrivo is not None and arrivo >= dal:
            out.append((arrivo, uid))
    return sorted(out)


def _leggi(client_factory: Any) -> EsitoLetturaCasella:
    c = parametri_casella()
    nome_casella = f"{c['USER']}@{c['HOST']}/{c['FOLDER']}"
    stato, _ = StatoCasella.objects.get_or_create(casella=nome_casella)
    esito = EsitoLetturaCasella(True)
    if stato.letto_fino_al is None:
        # Casella mai letta da Metodo v2: i messaggi già presenti sono ordini vecchi (di solito importati dal
        # Metodo precedente, che non li cancella); reimportarli riaprirebbe ordini chiusi. Si parte da adesso.
        # Per leggere da una data precisa: manage.py casella_ordini --dal AAAA-MM-GG
        stato.letto_fino_al, stato.ultima_lettura_il, stato.ultimo_errore = timezone.now(), timezone.now(), ""
        stato.save()
        logger.warning("Prima lettura di %s: si leggono i messaggi che arrivano da adesso", nome_casella)
        esito.motivo = "prima lettura: si parte dai messaggi che arrivano da adesso"
        return esito
    client = client_factory()
    try:
        # Sola lettura e BODY.PEEK: Metodo v2 non cambia mai la marca «letto» dei messaggi.
        info = client.select_folder(c["FOLDER"], readonly=True)
        uidvalidity = int(info[b"UIDVALIDITY"])
        stato.uidvalidity = uidvalidity
        nuovi = _nuovi(client, stato.letto_fino_al)
        gia_letti = set(
            MessaggioEmail.objects.filter(
                casella=nome_casella, uidvalidity=uidvalidity, uid__in=[u for _, u in nuovi]
            ).values_list("uid", flat=True)
        )
        max_byte = config.leggi("importazione.dimensione_massima_allegato_mb") * 1024 * 1024
        for arrivo, uid in nuovi[:MESSAGGI_PER_GIRO]:
            if uid not in gia_letti:  # già elaborato (stesso istante di arrivo o lettura ripetuta): si salta
                dati = client.fetch([uid], ["RFC822.SIZE"])[uid]
                if dati[b"RFC822.SIZE"] > max_byte * 2:
                    _registra(
                        nome_casella, uidvalidity, uid, None, arrivo, EsitoMessaggio.SCARTATO, "messaggio troppo grande"
                    )
                else:
                    corpo = client.fetch([uid], ["BODY.PEEK[]"])[uid][b"BODY[]"]
                    esito.file += _elabora_messaggio(
                        nome_casella, uidvalidity, uid, corpo, arrivo, max_byte, c["MITTENTI"]
                    )
                esito.messaggi += 1
            stato.letto_fino_al, stato.ultimo_uid = arrivo, uid
            stato.save(update_fields=["letto_fino_al", "ultimo_uid", "uidvalidity"])
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


def imposta_inizio(dal: dt.datetime | None = None) -> str:
    """Da quando si leggono i messaggi: «dal» (es. una data scelta) oppure da adesso. Non si collega alla casella:
    alla lettura successiva si prendono i messaggi arrivati da quel momento (quelli già elaborati si saltano)."""
    c = parametri_casella()
    nome_casella = f"{c['USER']}@{c['HOST']}/{c['FOLDER']}"
    StatoCasella.objects.update_or_create(
        casella=nome_casella, defaults={"letto_fino_al": dal or timezone.now(), "ultimo_errore": ""}
    )
    return nome_casella


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


def _mittente(msg: Any) -> str:
    return email.utils.parseaddr(str(msg.get("From", "")))[1].strip().lower()


def _elabora_messaggio(
    casella: str, uidvalidity: int, uid: int, raw: bytes, ricevuto: Any, max_byte: int, mittenti: Any = ()
) -> int:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    ammessi = {m.strip().lower() for m in (mittenti or [])}
    if ammessi and _mittente(msg) not in ammessi:
        _registra(
            casella, uidvalidity, uid, msg, ricevuto, EsitoMessaggio.SCARTATO, f"mittente non ammesso: {_mittente(msg)}"
        )
        return 0
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
