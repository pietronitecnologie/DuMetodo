"""Scorciatoie da tastiera del client desktop, definite sul server (Configurazione > Client desktop).

Formato (JSON): elenco di voci
    [{"tasto": "F2", "etichetta": "Cerca ordine", "azione": "cerca_ordine"},
     {"tasto": "F3", "etichetta": "Elenco ordini", "azione": "naviga", "url": "/ordini/", "permesso": "ordini_vedere"}]

- ``tasto``: F1-F12, oppure una combinazione come ``Ctrl+Shift+K`` / ``Alt+3`` (lettere, cifre, F1-F12);
- ``azione``: ``naviga`` (con ``url`` interno), ``cerca_ordine`` (cursore nella ricerca dell'ordine),
  ``nuova_scheda``, ``ricarica``, ``stampa``, ``indietro``, ``avanti``, ``schermo_intero``, ``aiuto``
  (elenco delle scorciatoie);
- ``permesso``: facoltativo, la scorciatoia vale solo per chi ha quella funzione.
Le scorciatoie del client (Ctrl+T, Ctrl+W, Ctrl+Tab, Ctrl+P, F11, zoom) non si possono ridefinire.
"""

from __future__ import annotations

import json
import re
from typing import Any

AZIONI = {
    "naviga",
    "cerca_ordine",
    "nuova_scheda",
    "ricarica",
    "stampa",
    "indietro",
    "avanti",
    "schermo_intero",
    "aiuto",
}
CAMPI = {"tasto", "etichetta", "azione", "url", "permesso"}
# Già usate dal client (schede, stampa, schermo intero, zoom).
RISERVATE = {"CTRL+T", "CTRL+W", "CTRL+TAB", "CTRL+SHIFT+TAB", "CTRL+P", "F11", "CTRL+0"}
_RE_TASTO = re.compile(r"^(?:(?:CTRL|ALT|SHIFT)\+){0,3}(?:F(?:[1-9]|1[0-2])|[A-Z0-9])$")

PREDEFINITE: list[dict[str, Any]] = [
    {"tasto": "F1", "etichetta": "Elenco delle scorciatoie", "azione": "aiuto"},
    {"tasto": "F2", "etichetta": "Cerca un ordine", "azione": "cerca_ordine", "permesso": "ordini_vedere"},
    {"tasto": "F3", "etichetta": "Elenco ordini", "azione": "naviga", "url": "/ordini/", "permesso": "ordini_vedere"},
    {
        "tasto": "F4",
        "etichetta": "Agenda ordini",
        "azione": "naviga",
        "url": "/pianificazione/agenda/",
        "permesso": "agenda",
    },
    {"tasto": "F6", "etichetta": "Consegna", "azione": "naviga", "url": "/consegne/", "permesso": "consegna"},
    {"tasto": "F7", "etichetta": "DDT", "azione": "naviga", "url": "/consegne/ddt/", "permesso": "ddt"},
    {
        "tasto": "F8",
        "etichetta": "Importazione ordini",
        "azione": "naviga",
        "url": "/importazione/",
        "permesso": "importazione",
    },
    {
        "tasto": "F9",
        "etichetta": "Richiesta materiali",
        "azione": "naviga",
        "url": "/magazzino/richieste/",
        "permesso": "magazzino_richieste",
    },
    {"tasto": "F10", "etichetta": "Dashboard", "azione": "naviga", "url": "/report/", "permesso": "report_dashboard"},
    {"tasto": "Ctrl+Shift+N", "etichetta": "Nuova scheda", "azione": "nuova_scheda"},
]
PREDEFINITE_JSON = json.dumps(PREDEFINITE, ensure_ascii=False, indent=2)


class ScorciatoieNonValide(ValueError):
    pass


def normalizza_tasto(tasto: str) -> str:
    """'ctrl + shift + k' -> 'CTRL+SHIFT+K' (modificatori in ordine fisso)."""
    parti = [p.strip().upper() for p in str(tasto).split("+") if p.strip()]
    modificatori = [m for m in ("CTRL", "ALT", "SHIFT") if m in parti]
    altri = [p for p in parti if p not in ("CTRL", "ALT", "SHIFT")]
    return "+".join([*modificatori, *altri])


def valida(testo: str) -> list[dict[str, Any]]:
    from dumetodo.core.permessi import FUNZIONI

    try:
        dati = json.loads(testo)
    except json.JSONDecodeError as exc:
        raise ScorciatoieNonValide(f"JSON non valido alla riga {exc.lineno}, colonna {exc.colno}: {exc.msg}.") from None
    if not isinstance(dati, list):
        raise ScorciatoieNonValide("Il contenuto deve essere un elenco: [ {voce}, {voce}, ... ].")
    permessi = {f.codice for f in FUNZIONI}
    visti: set[str] = set()
    for n, v in enumerate(dati, start=1):
        dove = f"voce {n}"
        if not isinstance(v, dict):
            raise ScorciatoieNonValide(f"{dove}: ogni voce è un oggetto.")
        if estranei := set(v) - CAMPI:
            raise ScorciatoieNonValide(f"{dove}: campi sconosciuti {', '.join(sorted(estranei))}.")
        tasto = normalizza_tasto(v.get("tasto", ""))
        if not _RE_TASTO.match(tasto):
            raise ScorciatoieNonValide(f"{dove}: tasto {v.get('tasto')!r} non valido (es. F2, Ctrl+Shift+K, Alt+3).")
        if tasto in RISERVATE:
            raise ScorciatoieNonValide(f"{dove}: {v.get('tasto')} è già usato dal client.")
        if not any(m in tasto for m in ("CTRL", "ALT")) and not tasto.split("+")[-1].startswith("F"):
            raise ScorciatoieNonValide(f"{dove}: una lettera o cifra da sola si scrive nei campi: usa Ctrl o Alt.")
        if tasto in visti:
            raise ScorciatoieNonValide(f"{dove}: tasto {v.get('tasto')} usato due volte.")
        visti.add(tasto)
        if not isinstance(v.get("etichetta"), str) or not v["etichetta"].strip():
            raise ScorciatoieNonValide(f"{dove}: manca l'etichetta.")
        if v.get("azione") not in AZIONI:
            ammesse = ", ".join(sorted(AZIONI))
            raise ScorciatoieNonValide(f"{dove}: azione {v.get('azione')!r} non valida; ammesse: {ammesse}.")
        if v["azione"] == "naviga":
            url = v.get("url")
            if not isinstance(url, str) or not url.startswith("/") or url.startswith("//") or "\\" in url:
                raise ScorciatoieNonValide(f"{dove}: l'url deve essere un percorso interno che inizia con '/'.")
        if "permesso" in v and v["permesso"] not in permessi:
            raise ScorciatoieNonValide(f"{dove}: permesso sconosciuto {v['permesso']!r}.")
    return dati


def per_utente(dati: list[dict[str, Any]], utente: Any) -> list[dict[str, Any]]:
    from dumetodo.core.permessi import ha_permesso

    return [
        {**{k: x for k, x in v.items() if k != "permesso"}, "tasto": normalizza_tasto(v["tasto"])}
        for v in dati
        if not v.get("permesso") or ha_permesso(utente, v["permesso"])
    ]
