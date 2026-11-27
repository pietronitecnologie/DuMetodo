"""Controlli di MetodoConsole per area: ogni area si attiva da sola se sul server trova ciò che controlla.

    from controlli import esegui_aree, Contesto
    esiti = esegui_aree(Contesto(ini), aree=None)   # tutte le aree trovate

Solo libreria standard (Python 3.10+). Nessun controllo modifica il server: leggono configurazioni, stato dei
servizi e log, e fanno qualche richiesta di rete (siti, porte, DNS).
"""

from __future__ import annotations

from .base import ATTENZIONE, ERRORE, NOMI_GRUPPI, OK, Contesto, Esito
from .registro import AREE, esegui_aree, rilevate

__all__ = ["AREE", "ATTENZIONE", "ERRORE", "NOMI_GRUPPI", "OK", "Contesto", "Esito", "esegui_aree", "rilevate"]
