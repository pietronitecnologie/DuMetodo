"""Manuale utente: il file Markdown docs/manuale-utente.md, servito così com'è e reso in HTML.

Ogni pagina dell'applicazione ha un piccolo "?" che apre il capitolo del manuale che la riguarda:
il capitolo si sceglie dal nome della vista (``CAPITOLI``, il primo prefisso che corrisponde).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

PERCORSO = Path(settings.BASE_DIR) / "docs" / "manuale-utente.md"

# (prefisso del nome della vista, ancora del capitolo). Vince il primo che corrisponde.
CAPITOLI: tuple[tuple[str, str], ...] = (
    ("home", "primi-passi"),
    ("utenti:mia_password", "accesso-e-password"),
    ("utenti:", "utenti-e-permessi"),
    ("reparti:", "reparti"),
    ("chat:", "chat"),
    ("ordini:", "ordini"),
    ("ticket:", "ticket"),
    ("comunicazioni:", "note-e-comunicazione-con-il-cliente"),
    ("pianificazione:agenda", "agenda"),
    ("pianificazione:tabulato", "tabulati"),
    ("pianificazione:", "pianificazione"),
    ("consegne:ddt", "ddt"),
    ("consegne:", "consegna"),
    ("importazione:", "importazione-ordini"),
    ("magazzino:richiest", "richiesta-materiali"),
    ("magazzino:inventario", "inventario"),
    ("magazzino:distint", "distinte-base"),
    ("magazzino:riga_distinta", "distinte-base"),
    ("magazzino:ddt", "ddt-in-ingresso"),
    ("magazzino:materiali", "ddt-in-ingresso"),
    ("magazzino:", "componenti-e-articoli"),
    ("report:", "report"),
    ("utilita:", "utilita"),
    ("configurazione:", "configurazione"),
    ("client:versioni", "aggiornamenti-del-client-desktop"),
    ("tracciatura:modell", "modelli-di-tracciatura"),
    ("tracciatura:", "tracciatura"),
    ("timbrature:postazion", "postazioni-di-timbratura"),
    ("timbrature:lavorator", "lavoratori-e-codici"),
    ("timbrature:costi", "costi-per-reparto"),
    ("timbrature:", "timbrature"),
    ("gestionale:ddt", "ddt-di-legge"),
    ("gestionale:da_fatturare", "fatture-elettroniche"),
    ("gestionale:fattur", "fatture-elettroniche"),
    ("gestionale:impostazioni", "dati-fiscali-e-impostazioni"),
    ("gestionale:numerazioni", "numerazioni"),
    ("gestionale:", "gestionale"),
)


def capitolo_per(nome_vista: str | None) -> str:
    """Ancora del capitolo per la vista; stringa vuota = inizio del manuale."""
    for prefisso, ancora in CAPITOLI:
        if nome_vista and nome_vista.startswith(prefisso):
            return ancora
    return ""


def testo() -> str:
    return PERCORSO.read_text(encoding="utf-8")


def html() -> tuple[str, str]:
    """(indice, corpo) in HTML; ricalcolato solo se il file cambia."""
    return _html(PERCORSO.stat().st_mtime_ns)


@lru_cache(maxsize=2)
def _html(_versione: int) -> tuple[str, str]:
    import markdown

    md = markdown.Markdown(
        extensions=["tables", "toc", "sane_lists"],
        extension_configs={"toc": {"toc_depth": "2-3"}},
    )
    corpo = md.convert(testo())
    # Il titolo del manuale (h1) è già nella pagina; tabelle con lo stile di Metodo v2.
    corpo = re.sub(r"<h1[^>]*>.*?</h1>", "", corpo, count=1, flags=re.S)
    corpo = corpo.replace("<table>", '<div class="table-responsive"><table class="table table-sm">').replace(
        "</table>", "</table></div>"
    )
    return md.toc, corpo  # type: ignore[attr-defined]


def ancore() -> set[str]:
    return set(re.findall(r'id="([^"]+)"', html()[1]))
