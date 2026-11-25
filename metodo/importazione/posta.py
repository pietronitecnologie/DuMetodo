"""Lettura della casella IMAP degli ordini.

- una sola esecuzione alla volta (advisory lock PostgreSQL), anche con più worker;
- cursore per UID con UIDVALIDITY; casella aperta in sola lettura (nessuna email cancellata);
- ogni messaggio è registrato (anche quelli senza allegati, che Metodo rileggeva all'infinito);
- deduplica per Message-ID, così uno spostamento di cartella non reimporta nulla;
- il cursore avanza dopo OGNI messaggio: un'interruzione non fa perdere né ripetere lavoro.
"""

from __future__ import annotations

import contextlib
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


def parametri_casella(modulo: dict[str, Any] | None = None, committente: Any = None) -> dict[str, Any]:
    """Dati della casella ordini: quelli di Configurazione se il server è impostato, altrimenti il file .env.

    ``modulo``: valori non ancora salvati (pulsante "Prova connessione"); password vuota = quella salvata.
    ``committente``: la casella propria di un cliente committente (Clienti committenti → Impostazioni).
    """
    m = modulo or {}

    def valore(chiave: str) -> Any:
        if f"casella.{chiave}" in m or (chiave == "ssl" and modulo is not None):
            p = config.REGISTRO[f"casella.{chiave}"]
            try:
                return p.converti(m.get(f"casella.{chiave}"))
            except ValidationError:
                return config.leggi(f"casella.{chiave}", committente)
        return config.leggi(f"casella.{chiave}", committente)

    server = (valore("server") or "").strip()
    if not server and committente is not None:
        return {"HOST": "", "PORT": 993, "SSL": True, "USER": "", "PASSWORD": "", "FOLDER": "INBOX", "MITTENTI": []}
    if not server:
        return {**settings.ORDINI_IMAP, "MITTENTI": config.leggi("casella.mittenti_ammessi"), "ORIGINE": ".env"}
    return {
        "HOST": server,
        "PORT": valore("porta") or 993,
        "SSL": bool(valore("ssl")),
        "USER": (valore("utente") or "").strip(),
        "PASSWORD": (m.get("casella.password") or "") or config.leggi("casella.password", committente) or "",
        "FOLDER": (valore("cartella") or "INBOX").strip(),
        "MITTENTI": valore("mittenti_ammessi") or [],
        "ORIGINE": "Configurazione",
    }


def _configurata(c: dict[str, Any]) -> bool:
    return bool(c["HOST"] and c["USER"] and c["PASSWORD"])


def caselle() -> list[tuple[Any, dict[str, Any]]]:
    """Le caselle da leggere: quella generale (committente ``None``) e quelle proprie dei committenti attivi
    non predefiniti. Il predefinito usa la casella generale."""
    from metodo.committenti.models import Committente

    elenco: list[tuple[Any, dict[str, Any]]] = []
    generale = parametri_casella()
    if _configurata(generale):
        elenco.append((None, generale))
    for c in Committente.objects.filter(attivo=True, predefinito=False).order_by("nome"):
        propria = parametri_casella(committente=c)
        if _configurata(propria):
            elenco.append((c, propria))
    return elenco


def casella_configurata() -> bool:
    return bool(caselle())


def instradamento_generale() -> Any:
    """Per la casella generale: dal mittente al committente.

    I mittenti indicati nelle impostazioni di un committente portano a lui; gli altri vanno al predefinito,
    se ammessi dalla Configurazione (elenco vuoto = tutti); altrimenti il messaggio è scartato (``None``)."""
    from metodo.committenti.models import Committente, predefinito_id

    per_mittente: dict[str, int] = {}
    for c in Committente.objects.filter(attivo=True, predefinito=False):
        if config.ha_valore_proprio("casella.mittenti_ammessi", c):
            for m in config.leggi("casella.mittenti_ammessi", c) or []:
                per_mittente.setdefault(m.strip().lower(), c.pk)
    ammessi = {m.strip().lower() for m in (config.leggi("casella.mittenti_ammessi") or [])}
    predefinito = predefinito_id()

    def committente_di(mittente: str) -> int | None:
        if mittente in per_mittente:
            return per_mittente[mittente]
        return predefinito if not ammessi or mittente in ammessi else None

    return committente_di


def instradamento_proprio(committente: Any, mittenti: Any) -> Any:
    """Per la casella di un committente: tutto a lui, dai mittenti indicati (elenco vuoto = tutti)."""
    ammessi = {m.strip().lower() for m in (mittenti or [])}
    return lambda mittente: committente.pk if not ammessi or mittente in ammessi else None


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
    elenco = caselle()
    if not elenco:
        return EsitoLetturaCasella(False, motivo="casella IMAP non configurata")
    with _lock_esclusivo() as ottenuto:
        if not ottenuto:
            return EsitoLetturaCasella(False, motivo="lettura già in corso")
        totale = EsitoLetturaCasella(True)
        errori = []
        for committente, c in elenco:
            instrada = (
                instradamento_generale() if committente is None else instradamento_proprio(committente, c["MITTENTI"])
            )
            try:
                esito = _leggi((lambda c=c: _client(c)) if client_factory is None else client_factory, c, instrada)
            except Exception as exc:  # una casella in errore non ferma le altre
                if len(elenco) == 1:
                    raise
                logger.exception("Lettura della casella %s non riuscita", c["HOST"])
                errori.append(f"{c['USER']}@{c['HOST']}: {type(exc).__name__}")
                continue
            totale.messaggi += esito.messaggi
            totale.file += esito.file
        totale.motivo = "; ".join(errori)
        return totale


def _leggi(client_factory: Any, c: dict[str, Any] | None = None, instrada: Any = None) -> EsitoLetturaCasella:
    c = c or parametri_casella()
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
                    nome_casella,
                    uidvalidity,
                    uid,
                    corpo,
                    dati.get(b"INTERNALDATE"),
                    max_byte,
                    c["MITTENTI"],
                    instrada=instrada,
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


def _mittente(msg: Any) -> str:
    return email.utils.parseaddr(str(msg.get("From", "")))[1].strip().lower()


def _elabora_messaggio(
    casella: str,
    uidvalidity: int,
    uid: int,
    raw: bytes,
    ricevuto: Any,
    max_byte: int,
    mittenti: Any = (),
    *,
    instrada: Any = None,
) -> int:
    """``instrada``: dal mittente al committente degli ordini (``None`` = scartato); senza, vale l'elenco
    ``mittenti`` e gli ordini sono del committente predefinito."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    if instrada is None:
        from metodo.committenti.models import predefinito_id

        ammessi = {m.strip().lower() for m in (mittenti or [])}
        committente_id = predefinito_id() if not ammessi or _mittente(msg) in ammessi else None
    else:
        committente_id = instrada(_mittente(msg))
    if committente_id is None:
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
        files = [
            servizi.registra_file(
                f.nome, f.contenuto, OrigineFile.EMAIL, messaggio=registrato, committente=committente_id
            )
            for f in estratti
        ]
    for f in files:
        servizi.importa(f)
    return len(files)
