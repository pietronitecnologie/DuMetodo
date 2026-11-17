"""Controlli all'avvio (``manage.py check --deploy``): segnalano configurazioni mancanti
prima che diventino errori 500 in produzione."""

from typing import Any

from django.core.checks import Tags, Warning, register
from django.db import DatabaseError


@register(Tags.database, deploy=True)
def controlla_parametri(app_configs: Any, **kwargs: Any) -> list[Warning]:
    from . import servizi
    from .registro import PARAMETRI

    avvisi = []
    try:
        for p in PARAMETRI:
            if p.legacy and p.default is None and servizi.leggi(p.chiave) in (None, [], ""):
                avvisi.append(
                    Warning(
                        f"Parametro non impostato: {p.etichetta}",
                        hint=f"Configuralo in /configurazione/ ({p.chiave}).",
                        id="configurazione.W001",
                    )
                )
    except DatabaseError:
        return []
    from dumetodo.importazione.posta import casella_configurata

    try:
        configurata = casella_configurata()
    except DatabaseError:
        configurata = True
    if not configurata:
        avvisi.append(
            Warning(
                "Casella IMAP ordini non configurata: la lettura automatica degli ordini è disattivata.",
                hint="Impostala in Configurazione > Casella ordini (IMAP).",
                id="configurazione.W002",
            )
        )
    return avvisi
