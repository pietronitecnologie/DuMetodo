"""Linguaggio dei TRACCIATI: descrive il formato dei file esportati (es. bolla per il gestionale).

Esempio (bolla ReadyPro)::

    # Le righe che iniziano con # sono commenti.
    nome: ReadyPro
    estensione: txt
    codifica: cp1252
    fine_riga: CRLF
    nome_file: consegna{documento.selezione}-{documento.oggi|data:AAAAMMGG}

    [ordine]
    Riferimento |ordine {ordine.serie}.{ordine.numero}

    [riga]
    {riga.articolo}{riga.variante|senza:_|prefisso:.}||{riga.qta|intero}|PZ|{riga.prezzo|compatto|virgola}

Struttura:
- in testa le IMPOSTAZIONI ``chiave: valore``;
- poi le SEZIONI, ognuna con una o più righe di testo in uscita:
  ``[intestazione]`` (una volta all'inizio), ``[ordine]`` (all'inizio di ogni ordine),
  ``[riga]`` (per ogni riga), ``[fine_ordine]`` (alla fine di ogni ordine), ``[chiusura]`` (una volta alla fine);
- nel testo, ``{campo|filtro|filtro:argomento}`` viene sostituito dal valore del campo trasformato
  dai filtri, da sinistra a destra; ``{"testo"}`` è un testo fisso; ``{{`` e ``}}`` producono ``{`` e ``}``.

Il linguaggio è volutamente limitato: niente codice, niente accesso al database, solo i campi
elencati in ``CAMPI``. Ogni errore (campo inesistente, filtro sbagliato, filtro numerico applicato
a un testo, ...) è segnalato quando si SALVA il tracciato, con il numero di riga.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

# ----------------------------------------------------------------------------------- schema dei dati
TESTO, NUMERO, DATA = "testo", "numero", "data"


@dataclass(frozen=True)
class Campo:
    tipo: str
    descrizione: str


CAMPI: dict[str, dict[str, Campo]] = {
    "documento": {
        "oggi": Campo(DATA, "Data di generazione del file"),
        "ora": Campo(DATA, "Data e ora di generazione del file"),
        "selezione": Campo(TESTO, "Selezione della consegna, es. 'Tutto' o '40'"),
        "utente": Campo(TESTO, "Utente che genera il file"),
        "numero": Campo(TESTO, "Numero del documento (es. numero DDT); vuoto se non previsto"),
        "data": Campo(DATA, "Data del documento (es. data DDT); se non prevista è la data di oggi"),
        "numero_ordini": Campo(NUMERO, "Numero di ordini nel file"),
        "numero_righe": Campo(NUMERO, "Numero di righe nel file"),
    },
    "ordine": {
        "serie": Campo(TESTO, "Serie, es. 00040"),
        "numero": Campo(TESTO, "Numero ordine, es. 0004300"),
        "appendice": Campo(TESTO, "Appendice (vuota se assente)"),
        "anno": Campo(NUMERO, "Anno"),
        "codice": Campo(TESTO, "Codice completo serie.numero[.appendice]-anno"),
        "codice_breve": Campo(TESTO, "Codice breve, es. 40.0004300-2026"),
        "cliente": Campo(TESTO, "Codice cliente"),
        "data_scadenza": Campo(DATA, "Scadenza dell'ordine"),
        "progressivo": Campo(NUMERO, "Posizione dell'ordine nel file (1, 2, ...)"),
        "numero_righe": Campo(NUMERO, "Righe dell'ordine nel file"),
        "totale_qta": Campo(NUMERO, "Somma delle quantità delle righe nel file"),
    },
    "riga": {
        "n_riga": Campo(TESTO, "Numero di riga, es. 0000001"),
        "articolo": Campo(TESTO, "Codice articolo"),
        "variante": Campo(TESTO, "Variante (vuota se assente)"),
        "qta": Campo(NUMERO, "Quantità"),
        "prezzo": Campo(NUMERO, "Prezzo unitario"),
        "sconto": Campo(NUMERO, "Sconto unitario"),
        "importo": Campo(NUMERO, "Quantità × prezzo"),
        "cliente": Campo(TESTO, "Codice cliente della riga"),
        "magazzino": Campo(TESTO, "Magazzino"),
        "data_scadenza": Campo(DATA, "Scadenza della riga"),
        "progressivo": Campo(NUMERO, "Posizione della riga nell'ordine (1, 2, ...)"),
        "progressivo_file": Campo(NUMERO, "Posizione della riga nel file (1, 2, ...)"),
    },
}

SEZIONI: dict[str, tuple[str, ...]] = {  # sezione -> gruppi di campi utilizzabili
    "intestazione": ("documento",),
    "ordine": ("documento", "ordine"),
    "riga": ("documento", "ordine", "riga"),
    "fine_ordine": ("documento", "ordine"),
    "chiusura": ("documento",),
}

CODIFICHE = {"utf-8": "utf-8", "utf-8-bom": "utf-8-sig", "cp1252": "cp1252", "latin-1": "latin-1", "ascii": "ascii"}
FINE_RIGA = {"CRLF": "\r\n", "LF": "\n"}
IMPOSTAZIONI = {
    "nome": "Nome del tracciato",
    "estensione": "Estensione del file (default txt)",
    "codifica": f"Codifica dei caratteri: {', '.join(CODIFICHE)} (default utf-8)",
    "fine_riga": "CRLF (Windows, default) o LF",
    "nome_file": "Nome del file senza estensione; può usare i campi documento",
}


class ErroreTracciato(ValueError):
    def __init__(self, messaggio: str, riga: int | None = None) -> None:
        self.riga = riga
        super().__init__(f"Riga {riga}: {messaggio}" if riga else messaggio)


# ----------------------------------------------------------------------------------- filtri
@dataclass(frozen=True)
class Filtro:
    accetta: frozenset[str]  # tipi di ingresso ammessi
    produce: str | None  # tipo prodotto (None = stesso tipo in ingresso)
    argomenti: tuple[int, int]  # (minimo, massimo)
    descrizione: str
    esempio: str
    fn: Callable[..., Any]


_TUTTI = frozenset({TESTO, NUMERO, DATA})


def _testo(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, dt.datetime):
        return v.strftime("%d/%m/%Y %H:%M")
    if isinstance(v, dt.date):
        return v.strftime("%d/%m/%Y")
    return str(v)


def _intero(n: str) -> int:
    if not n.isdigit():
        raise ValueError(f"'{n}' non è un numero intero")
    return int(n)


def _numero(v: Any, fn: Callable[[Decimal], Decimal]) -> Decimal | None:
    return None if v is None else fn(Decimal(v))


def _data(v: Any, formato: str) -> str:
    if v is None:
        return ""
    sostituzioni = [("AAAA", "%Y"), ("AA", "%y"), ("MM", "%m"), ("GG", "%d"), ("hh", "%H"), ("mm", "%M"), ("ss", "%S")]
    fmt = formato.replace("%", "%%")
    for token, codice in sostituzioni:
        fmt = fmt.replace(token, codice)
    return v.strftime(fmt)


def _pad(v: Any, n: str, c: str, sinistra: bool) -> str:
    s, larghezza = _testo(v), _intero(n)
    if len(c) != 1:
        raise ValueError("il carattere di riempimento deve essere uno solo")
    return s.rjust(larghezza, c) if sinistra else s.ljust(larghezza, c)


def _senza_zeri(v: Any) -> str:
    s = _testo(v)
    return (s.lstrip("0") or "0") if s else ""


FILTRI: dict[str, Filtro] = {
    # testo
    "maiuscolo": Filtro(_TUTTI, TESTO, (0, 0), "Tutto maiuscolo", "abc → ABC", lambda v: _testo(v).upper()),
    "minuscolo": Filtro(_TUTTI, TESTO, (0, 0), "Tutto minuscolo", "ABC → abc", lambda v: _testo(v).lower()),
    "spazi": Filtro(
        _TUTTI, TESTO, (0, 0), "Toglie spazi iniziali e finali", "' a ' → 'a'", lambda v: _testo(v).strip()
    ),
    "senza": Filtro(
        _TUTTI,
        TESTO,
        (1, 1),
        "Toglie tutti i caratteri indicati",
        "senza:_ : __0 → 0",
        lambda v, c: "".join(ch for ch in _testo(v) if ch not in c),
    ),
    "sostituisci": Filtro(
        _TUTTI,
        TESTO,
        (2, 2),
        "Sostituisce un testo con un altro",
        "sostituisci:/,- : 1/2 → 1-2",
        lambda v, a, b: _testo(v).replace(a, b),
    ),
    "tronca": Filtro(
        _TUTTI,
        TESTO,
        (1, 1),
        "Tiene al massimo N caratteri",
        "tronca:3 : ABCDE → ABC",
        lambda v, n: _testo(v)[: _intero(n)],
    ),
    "sinistra": Filtro(
        _TUTTI,
        TESTO,
        (1, 2),
        "Allinea a destra riempiendo a sinistra fino a N caratteri (default spazio)",
        "sinistra:5,0 : 42 → 00042",
        lambda v, n, c=" ": _pad(v, n, c, True),
    ),
    "destra": Filtro(
        _TUTTI,
        TESTO,
        (1, 2),
        "Riempie a destra fino a N caratteri (default spazio)",
        "destra:5 : ab → 'ab   '",
        lambda v, n, c=" ": _pad(v, n, c, False),
    ),
    "prefisso": Filtro(
        _TUTTI,
        TESTO,
        (1, 1),
        "Aggiunge un testo davanti, solo se il valore non è vuoto",
        "prefisso:. : 0 → .0 ; vuoto → vuoto",
        lambda v, p: p + _testo(v) if _testo(v) else "",
    ),
    "suffisso": Filtro(
        _TUTTI,
        TESTO,
        (1, 1),
        "Aggiunge un testo dopo, solo se il valore non è vuoto",
        "suffisso:PZ : 3 → 3PZ",
        lambda v, s: _testo(v) + s if _testo(v) else "",
    ),
    "predefinito": Filtro(
        _TUTTI,
        TESTO,
        (1, 1),
        "Usa questo testo se il valore è vuoto",
        "predefinito:- : vuoto → -",
        lambda v, d: _testo(v) or d,
    ),
    "uguale": Filtro(
        _TUTTI,
        TESTO,
        (3, 3),
        "Se il valore è uguale al primo argomento scrive il secondo, altrimenti il terzo",
        "uguale:1,T+R,RIG : 1 → T+R ; 2 → RIG",
        lambda v, x, si, no: si if _testo(v) == x else no,
    ),
    "senza_zeri": Filtro(_TUTTI, TESTO, (0, 0), "Toglie gli zeri iniziali", "0004300 → 4300", _senza_zeri),
    # numeri
    "intero": Filtro(
        frozenset({NUMERO}),
        NUMERO,
        (0, 0),
        "Parte intera (tronca i decimali)",
        "2,75 → 2",
        lambda v: _numero(v, lambda d: d.to_integral_value(rounding=ROUND_DOWN)),
    ),
    "arrotonda": Filtro(
        frozenset({NUMERO}),
        NUMERO,
        (1, 1),
        "Arrotonda a N decimali (sempre N cifre)",
        "arrotonda:2 : 2,5 → 2.50",
        lambda v, n: _numero(v, lambda d: d.quantize(Decimal(1).scaleb(-_intero(n)), ROUND_HALF_UP)),
    ),
    "compatto": Filtro(
        frozenset({NUMERO}),
        NUMERO,
        (0, 0),
        "Toglie gli zeri decimali inutili",
        "2.500 → 2.5 ; 3.000 → 3",
        lambda v: _numero(v, lambda d: d.normalize()),
    ),
    "virgola": Filtro(
        frozenset({NUMERO, TESTO}),
        TESTO,
        (0, 0),
        "Usa la virgola come separatore decimale",
        "2.5 → 2,5",
        lambda v: _testo(v).replace(".", ","),
    ),
    # date
    "data": Filtro(
        frozenset({DATA}),
        TESTO,
        (1, 1),
        "Formatta una data: AAAA anno, AA anno a 2 cifre, MM mese, GG giorno, hh ore, mm minuti, ss secondi",
        "data:GG/MM/AAAA : 02/04/2026",
        _data,
    ),
}


# ----------------------------------------------------------------------------------- compilazione
@dataclass(frozen=True)
class _Segnaposto:
    origine: tuple[str, str] | None  # (gruppo, campo) oppure None per testo fisso
    fisso: str
    filtri: tuple[tuple[str, tuple[str, ...]], ...]


Pezzo = str | _Segnaposto


@dataclass
class Tracciato:
    nome: str = ""
    estensione: str = "txt"
    codifica: str = "utf-8"
    fine_riga: str = "\r\n"
    nome_file: list[Pezzo] = field(default_factory=list)
    sezioni: dict[str, list[list[Pezzo]]] = field(default_factory=dict)


_RE_SEZIONE = re.compile(r"^\[([a-z_]+)\]\s*$")
_RE_IMPOSTAZIONE = re.compile(r"^([a-z_]+)\s*:\s*(.*?)\s*$")


def _dividi_argomenti(testo: str) -> tuple[str, ...]:
    """Argomenti separati da virgola; un argomento tra virgolette può contenere virgole e due punti."""
    if testo == "":
        return ("",)
    argomenti, corrente, tra_virgolette, i = [], "", False, 0
    while i < len(testo):
        ch = testo[i]
        if ch == '"':
            tra_virgolette = not tra_virgolette
        elif ch == "," and not tra_virgolette:
            argomenti.append(corrente)
            corrente = ""
        else:
            corrente += ch
        i += 1
    if tra_virgolette:
        raise ValueError("virgolette non chiuse negli argomenti")
    argomenti.append(corrente)
    return tuple(argomenti)


def _dividi_filtri(espressione: str) -> list[str]:
    parti, corrente, tra_virgolette = [], "", False
    for ch in espressione:
        if ch == '"':
            tra_virgolette = not tra_virgolette
        if ch == "|" and not tra_virgolette:
            parti.append(corrente)
            corrente = ""
        else:
            corrente += ch
    parti.append(corrente)
    return parti


def _compila_segnaposto(espressione: str, gruppi: tuple[str, ...], n: int) -> _Segnaposto:
    parti = _dividi_filtri(espressione)
    sorgente = parti[0].strip()
    if len(sorgente) >= 2 and sorgente[0] == sorgente[-1] == '"':
        origine, fisso, tipo = None, sorgente[1:-1], TESTO
    else:
        if "." not in sorgente:
            raise ErroreTracciato(f"campo '{sorgente}' non valido: scrivi gruppo.campo, es. riga.articolo", n)
        gruppo, nome = sorgente.split(".", 1)
        if gruppo not in CAMPI or nome not in CAMPI[gruppo]:
            raise ErroreTracciato(f"campo sconosciuto '{sorgente}'", n)
        if gruppo not in gruppi:
            raise ErroreTracciato(f"il campo '{sorgente}' non è disponibile in questa sezione", n)
        origine, fisso, tipo = (gruppo, nome), "", CAMPI[gruppo][nome].tipo
    filtri = []
    for grezzo in parti[1:]:
        nome_filtro, _, args_testo = grezzo.strip().partition(":")
        nome_filtro = nome_filtro.strip()
        filtro = FILTRI.get(nome_filtro)
        if filtro is None:
            raise ErroreTracciato(f"filtro sconosciuto '{nome_filtro}'", n)
        try:
            args = _dividi_argomenti(args_testo) if ":" in grezzo else ()
        except ValueError as exc:
            raise ErroreTracciato(f"filtro '{nome_filtro}': {exc}", n) from None
        minimo, massimo = filtro.argomenti
        if not minimo <= len(args) <= massimo:
            atteso = str(minimo) if minimo == massimo else f"da {minimo} a {massimo}"
            raise ErroreTracciato(f"il filtro '{nome_filtro}' vuole {atteso} argomenti, ne ha {len(args)}", n)
        if tipo not in filtro.accetta:
            raise ErroreTracciato(f"il filtro '{nome_filtro}' non si applica a un valore di tipo {tipo}", n)
        if nome_filtro in ("tronca", "sinistra", "destra", "arrotonda"):
            try:
                _intero(args[0])
            except ValueError as exc:
                raise ErroreTracciato(f"filtro '{nome_filtro}': {exc}", n) from None
        if nome_filtro in ("sinistra", "destra") and len(args) == 2 and len(args[1]) != 1:
            raise ErroreTracciato(f"filtro '{nome_filtro}': il carattere di riempimento deve essere uno solo", n)
        tipo = filtro.produce or tipo
        filtri.append((nome_filtro, args))
    return _Segnaposto(origine, fisso, tuple(filtri))


def _compila_testo(testo: str, gruppi: tuple[str, ...], n: int) -> list[Pezzo]:
    pezzi: list[Pezzo] = []
    letterale, i = "", 0
    while i < len(testo):
        ch = testo[i]
        if testo.startswith("{{", i) or testo.startswith("}}", i):
            letterale += ch
            i += 2
        elif ch == "{":
            fine = testo.find("}", i)
            if fine == -1:
                raise ErroreTracciato("graffa '{' non chiusa", n)
            if letterale:
                pezzi.append(letterale)
                letterale = ""
            pezzi.append(_compila_segnaposto(testo[i + 1 : fine], gruppi, n))
            i = fine + 1
        elif ch == "}":
            raise ErroreTracciato("graffa '}' senza apertura (per scriverla usa '}}')", n)
        else:
            letterale += ch
            i += 1
    if letterale:
        pezzi.append(letterale)
    return pezzi


def compila(sorgente: str) -> Tracciato:
    t = Tracciato()
    sezione: str | None = None
    viste: set[str] = set()
    for n, riga in enumerate(sorgente.splitlines(), start=1):
        if not riga.strip() or riga.lstrip().startswith("#"):
            continue
        if m := _RE_SEZIONE.match(riga.strip()):
            sezione = m.group(1)
            if sezione not in SEZIONI:
                raise ErroreTracciato(f"sezione sconosciuta [{sezione}]; valide: {', '.join(SEZIONI)}", n)
            if sezione in viste:
                raise ErroreTracciato(f"sezione [{sezione}] ripetuta", n)
            viste.add(sezione)
            t.sezioni[sezione] = []
            continue
        if sezione is None:
            m = _RE_IMPOSTAZIONE.match(riga)
            if not m or m.group(1) not in IMPOSTAZIONI:
                raise ErroreTracciato(
                    f"impostazione non valida: '{riga.strip()}'; valide: {', '.join(IMPOSTAZIONI)}", n
                )
            chiave, valore = m.groups()
            if chiave == "nome":
                t.nome = valore
            elif chiave == "estensione":
                if not re.fullmatch(r"[A-Za-z0-9]{1,10}", valore):
                    raise ErroreTracciato("estensione non valida", n)
                t.estensione = valore
            elif chiave == "codifica":
                if valore not in CODIFICHE:
                    raise ErroreTracciato(f"codifica '{valore}' non valida; valide: {', '.join(CODIFICHE)}", n)
                t.codifica = CODIFICHE[valore]
            elif chiave == "fine_riga":
                if valore not in FINE_RIGA:
                    raise ErroreTracciato("fine_riga deve essere CRLF o LF", n)
                t.fine_riga = FINE_RIGA[valore]
            elif chiave == "nome_file":
                t.nome_file = _compila_testo(valore, ("documento",), n)
            continue
        t.sezioni[sezione].append(_compila_testo(riga.rstrip("\r"), SEZIONI[sezione], n))
    if not t.sezioni.get("riga"):
        raise ErroreTracciato("manca la sezione [riga] (o è vuota): il file non conterrebbe le righe")
    return t


# ----------------------------------------------------------------------------------- generazione
@dataclass
class FileGenerato:
    nome: str
    contenuto: bytes
    testo: str


def _valuta(pezzi: list[Pezzo], dati: Mapping[str, Mapping[str, Any]]) -> str:
    out = []
    for p in pezzi:
        if isinstance(p, str):
            out.append(p)
            continue
        valore: Any = p.fisso if p.origine is None else dati[p.origine[0]].get(p.origine[1])
        for nome, args in p.filtri:
            valore = FILTRI[nome].fn(valore, *args)
        out.append(_testo(valore))
    return "".join(out)


def _nome_file_sicuro(nome: str) -> str:
    nome = re.sub(r"[^A-Za-z0-9._ -]", "_", nome).strip(" .")
    return nome[:150] or "esportazione"


def genera(
    tracciato: Tracciato,
    documento: Mapping[str, Any],
    ordini: Iterable[tuple[Mapping[str, Any], Iterable[Mapping[str, Any]]]],
) -> FileGenerato:
    """Genera il file. ``ordini`` = sequenza di (dati ordine, righe dell'ordine)."""
    ordini = [(o, list(r)) for o, r in ordini]
    documento = {**documento, "numero_ordini": len(ordini), "numero_righe": sum(len(r) for _, r in ordini)}
    righe_out: list[str] = []

    def emetti(sezione: str, dati: Mapping[str, Mapping[str, Any]]) -> None:
        for pezzi in tracciato.sezioni.get(sezione, []):
            righe_out.append(_valuta(pezzi, dati))

    emetti("intestazione", {"documento": documento})
    progressivo_file = 0
    for i, (ordine, righe) in enumerate(ordini, start=1):
        ordine = {
            **ordine,
            "progressivo": i,
            "numero_righe": len(righe),
            "totale_qta": sum((Decimal(r.get("qta") or 0) for r in righe), Decimal(0)),
        }
        emetti("ordine", {"documento": documento, "ordine": ordine})
        for j, riga in enumerate(righe, start=1):
            progressivo_file += 1
            riga = {**riga, "progressivo": j, "progressivo_file": progressivo_file}
            emetti("riga", {"documento": documento, "ordine": ordine, "riga": riga})
        emetti("fine_ordine", {"documento": documento, "ordine": ordine})
    emetti("chiusura", {"documento": documento})

    testo = "".join(r + tracciato.fine_riga for r in righe_out)
    try:
        contenuto = testo.encode(tracciato.codifica)
    except UnicodeEncodeError as exc:
        carattere = exc.object[exc.start : exc.end]
        raise ErroreTracciato(
            f"il carattere {carattere!r} non è rappresentabile nella codifica del tracciato ({tracciato.codifica})"
        ) from None
    nome = _nome_file_sicuro(_valuta(tracciato.nome_file, {"documento": documento}) or tracciato.nome or "esportazione")
    return FileGenerato(f"{nome}.{tracciato.estensione}", contenuto, testo)
