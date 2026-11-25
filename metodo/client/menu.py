"""Menu del tasto destro del client desktop, definiti sul server.

Il client Rust non conosce i menu: quando l'utente fa clic destro su un elemento marcato con
``data-dm-menu="<tipo>"`` (e attributi ``data-dm-<nome>`` con i dati), lo script ``client.js``
servito da Metodo v2 compone le voci per quel tipo (più quelle generiche della pagina), le invia al
client che mostra un menu NATIVO, e alla scelta esegue l'azione nella pagina.

Formato (JSON, modificabile in Configurazione > Client desktop)::

    {"pagina": [{"etichetta": "Ricarica", "azione": "ricarica"}],
     "ordine": [{"etichetta": "Apri ordine", "azione": "naviga", "url": "/ordini/{id}/"},
                {"separatore": true},
                {"etichetta": "Copia codice", "azione": "copia", "testo": "{codice}"}]}

Azioni: naviga, post, copia, stampa, ricarica, indietro, avanti, schermo_intero, e quelle sul punto
cliccato: copia_tabella, copia_colonna, copia_riga (formato incollabile in Excel), copia_selezione,
incolla, nuova_scheda, apri_in_nuova_scheda. Queste compaiono solo quando servono (su una tabella, con del
testo selezionato, su un campo in cui si può scrivere, su un link).
- ``url`` deve essere un percorso interno (inizia con "/", non con "//"): niente siti esterni;
- ``post`` invia ``campi`` con il token CSRF; ``conferma`` facoltativa chiede conferma prima;
- ``permesso``: codice di una funzione (Utenti e permessi); la voce compare solo a chi ce l'ha;
- ``{nome}`` viene sostituito con l'attributo ``data-dm-nome`` dell'elemento cliccato.
"""

from __future__ import annotations

import json
import re
from typing import Any

AZIONI = {
    "naviga",
    "post",
    "copia",
    "stampa",
    "ricarica",
    "indietro",
    "avanti",
    "schermo_intero",
    "copia_tabella",
    "copia_colonna",
    "copia_riga",
    "copia_selezione",
    "incolla",
    "nuova_scheda",
    "apri_in_nuova_scheda",
}
CAMPI_VOCE = {"etichetta", "azione", "url", "campi", "testo", "conferma", "permesso", "separatore"}
_RE_TIPO = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_RE_SEGNAPOSTO = re.compile(r"\{([a-z][a-z0-9_]*)\}")

PREDEFINITO: dict[str, list[dict[str, Any]]] = {
    "pagina": [
        {"etichetta": "Apri in nuova scheda", "azione": "apri_in_nuova_scheda"},
        {"etichetta": "Nuova scheda", "azione": "nuova_scheda"},
        {"separatore": True},
        {"etichetta": "Aggiorna pagina", "azione": "ricarica"},
        {"separatore": True},
        {"etichetta": "Copia tabella", "azione": "copia_tabella"},
        {"etichetta": "Copia colonna", "azione": "copia_colonna"},
        {"etichetta": "Copia riga", "azione": "copia_riga"},
        {"etichetta": "Copia selezione", "azione": "copia_selezione"},
        {"separatore": True},
        {"etichetta": "Incolla", "azione": "incolla"},
        {"separatore": True},
        {"etichetta": "Indietro", "azione": "indietro"},
        {"etichetta": "Avanti", "azione": "avanti"},
        {"etichetta": "Stampa pagina", "azione": "stampa"},
        {"etichetta": "Schermo intero", "azione": "schermo_intero"},
    ],
    "ordine": [
        {"etichetta": "Apri ordine", "azione": "naviga", "url": "/ordini/{id}/"},
        {"etichetta": "Copia codice ordine", "azione": "copia", "testo": "{codice}"},
        {"separatore": True},
        {
            "etichetta": "Cambia ordine…",
            "azione": "naviga",
            "url": "/utilita/cambia-ordine/{id}/",
            "permesso": "utilita_cambia_ordine",
        },
    ],
    "riga": [
        {
            "etichetta": "Evadi",
            "azione": "post",
            "url": "/ordini/{ordine}/righe/{riga}/azione/",
            "campi": {"azione": "evadi"},
            "permesso": "ordini_righe",
        },
        {
            "etichetta": "Blocca",
            "azione": "post",
            "url": "/ordini/{ordine}/righe/{riga}/azione/",
            "campi": {"azione": "blocca"},
            "permesso": "ordini_righe",
        },
        {
            "etichetta": "Scorpora / reincorpora",
            "azione": "post",
            "url": "/ordini/{ordine}/righe/{riga}/azione/",
            "campi": {"azione": "{scorporo}"},
            "permesso": "ordini_righe",
        },
        {"separatore": True},
        {
            "etichetta": "Dividi riga…",
            "azione": "naviga",
            "url": "/ordini/{ordine}/righe/{riga}/dividi/",
            "permesso": "ordini_dividi",
        },
        {"etichetta": "Copia articolo", "azione": "copia", "testo": "{articolo}"},
    ],
}


class MenuNonValido(ValueError):
    pass


def valida(testo: str) -> dict[str, list[dict[str, Any]]]:
    """Controlla il JSON dei menu; solleva MenuNonValido con un messaggio comprensibile."""
    from metodo.core.permessi import FUNZIONI

    try:
        dati = json.loads(testo)
    except json.JSONDecodeError as exc:
        raise MenuNonValido(f"JSON non valido alla riga {exc.lineno}, colonna {exc.colno}: {exc.msg}.") from None
    if not isinstance(dati, dict):
        raise MenuNonValido('Il contenuto deve essere un oggetto: {"tipo": [voci...]}.')
    permessi = {f.codice for f in FUNZIONI}
    for tipo, voci in dati.items():
        if not _RE_TIPO.match(str(tipo)):
            raise MenuNonValido(f"Tipo di menu non valido: {tipo!r} (minuscole, cifre e _).")
        if not isinstance(voci, list):
            raise MenuNonValido(f"{tipo}: serve un elenco di voci.")
        for n, v in enumerate(voci, start=1):
            dove = f"{tipo}, voce {n}"
            if not isinstance(v, dict):
                raise MenuNonValido(f"{dove}: ogni voce è un oggetto.")
            estranei = set(v) - CAMPI_VOCE
            if estranei:
                raise MenuNonValido(f"{dove}: campi sconosciuti {', '.join(sorted(estranei))}.")
            if v.get("separatore"):
                continue
            if not isinstance(v.get("etichetta"), str) or not v["etichetta"].strip():
                raise MenuNonValido(f"{dove}: manca l'etichetta.")
            azione = v.get("azione")
            if azione not in AZIONI:
                raise MenuNonValido(f"{dove}: azione {azione!r} non valida; ammesse: {', '.join(sorted(AZIONI))}.")
            if azione in ("naviga", "post"):
                url = v.get("url")
                if not isinstance(url, str) or not url.startswith("/") or url.startswith("//") or "\\" in url:
                    raise MenuNonValido(f"{dove}: l'url deve essere un percorso interno che inizia con '/'.")
            if azione == "copia" and not isinstance(v.get("testo"), str):
                raise MenuNonValido(f"{dove}: l'azione copia richiede 'testo'.")
            if "campi" in v and (
                not isinstance(v["campi"], dict) or not all(isinstance(x, str) for x in v["campi"].values())
            ):
                raise MenuNonValido(f"{dove}: 'campi' deve essere un oggetto di testi.")
            if "permesso" in v and v["permesso"] not in permessi:
                raise MenuNonValido(f"{dove}: permesso sconosciuto {v['permesso']!r}.")
    return dati


def per_utente(dati: dict[str, list[dict[str, Any]]], utente: Any) -> dict[str, list[dict[str, Any]]]:
    """Solo le voci che l'utente può usare; separatori in testa/coda o doppi rimossi."""
    from metodo.core.permessi import ha_permesso

    risultato: dict[str, list[dict[str, Any]]] = {}
    for tipo, voci in dati.items():
        filtrate = [v for v in voci if not v.get("permesso") or ha_permesso(utente, v["permesso"])]
        pulite: list[dict[str, Any]] = []
        for v in filtrate:
            if v.get("separatore") and (not pulite or pulite[-1].get("separatore")):
                continue
            pulite.append({k: x for k, x in v.items() if k != "permesso"})
        while pulite and pulite[-1].get("separatore"):
            pulite.pop()
        if pulite:
            risultato[tipo] = pulite
    return risultato


def segnaposti(dati: dict[str, list[dict[str, Any]]]) -> set[str]:
    trovati: set[str] = set()
    for voci in dati.values():
        for v in voci:
            for testo in (v.get("url", ""), v.get("testo", ""), *(v.get("campi") or {}).values()):
                trovati |= set(_RE_SEGNAPOSTO.findall(testo))
    return trovati
