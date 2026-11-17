"""Cifratura dei parametri segreti (es. password della casella ordini) salvati dall'app.

Le password non sono mai salvate in chiaro: si cifrano con Fernet (AES + HMAC) usando una chiave
ricavata da ``DUMETODO_CHIAVE_SEGRETI`` del file .env oppure, se manca, da ``DJANGO_SECRET_KEY``.
Chi legge il database (o un suo backup) senza il file .env non può ricavarle.

Se la chiave cambia, i segreti salvati non sono più leggibili: valgono come "non impostati" e vanno
reinseriti in Configurazione (lo segnala un avviso nel log).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    base = os.environ.get("DUMETODO_CHIAVE_SEGRETI") or settings.SECRET_KEY
    chiave = hashlib.sha256(f"dumetodo-segreti:{base}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(chiave))


def cifra(testo: str) -> str:
    return _fernet().encrypt(testo.encode()).decode()


def decifra(cifrato: str) -> str | None:
    try:
        return _fernet().decrypt(cifrato.encode()).decode()
    except (InvalidToken, ValueError):
        logger.warning("Un parametro segreto non è decifrabile (chiave cambiata?): va reinserito in Configurazione.")
        return None
