"""Pannello di MetodoConsole: controllo di Docker a schermo intero, da console o via SSH.

    metodoconsole pannello      (oppure solo «metodoconsole» da un terminale)

Solo libreria standard (curses). Mostra i container di Metodo v2 e gli altri presenti sul server (per esempio
quelli rimasti di una vecchia installazione), con stato, salute, porte e, a richiesta, CPU e memoria. Da qui si
avviano, fermano e riavviano i container, si leggono i log, si lancia la diagnostica, il backup e
l'aggiornamento. Le operazioni lunghe escono dal pannello e mostrano l'output di docker; con Invio si torna.

Le parti che non usano curses (lettura di ``docker ps``, stato dei container, filtri sui log) sono funzioni
semplici, provate dai test senza Docker.
"""

from __future__ import annotations

import contextlib
import curses
import json
import locale
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

AGGIORNA_OGNI = 3.0  # secondi tra due letture di «docker ps»
RIGHE_LOG = 2000
ORDINE_SERVIZI = ("db", "redis", "migrate", "web", "worker", "beat")
PAROLE_ERRORE = ("Traceback", " ERROR", "CRITICAL", "Error:", "Exception", "FATAL", "panic:")

# Stati (anche nomi dei colori)
OK, AVVIO, ERRORE, FERMO = "ok", "avvio", "errore", "fermo"


# =============================================================================================== dati
@dataclass
class Container:
    id: str
    nome: str
    immagine: str
    stato: str  # running, exited, created, restarting, paused, dead
    dettaglio: str  # «Up 3 hours (healthy)», «Exited (1) 2 minutes ago»…
    porte: str = ""
    progetto: str = ""
    servizio: str = ""
    cpu: str = ""
    memoria: str = ""

    @property
    def salute(self) -> str:
        m = re.search(r"\((healthy|unhealthy|health: starting)\)", self.dettaglio)
        return {"health: starting": "starting"}.get(m.group(1), m.group(1)) if m else ""

    @property
    def codice_uscita(self) -> int | None:
        m = re.search(r"Exited \((\d+)\)", self.dettaglio)
        return int(m.group(1)) if m else None

    @property
    def etichetta(self) -> str:
        return self.servizio or self.nome


def _etichette(testo: str) -> dict[str, str]:
    """«a=1,b=2» → {"a": "1", "b": "2"} (il formato delle Labels di ``docker ps``)."""
    out = {}
    for parte in (testo or "").split(","):
        k, _, v = parte.partition("=")
        if k:
            out[k.strip()] = v.strip()
    return out


def leggi_container(testo: str) -> list[Container]:
    """Uscita di ``docker ps --all --no-trunc --format json`` (una riga JSON per container, o una lista)."""
    testo = (testo or "").strip()
    if not testo:
        return []
    elementi = json.loads(testo) if testo.startswith("[") else [json.loads(r) for r in testo.splitlines() if r.strip()]
    out = []
    for e in elementi:
        lab = e.get("Labels") if isinstance(e.get("Labels"), dict) else _etichette(e.get("Labels", ""))
        out.append(
            Container(
                id=str(e.get("ID", ""))[:12],
                nome=str(e.get("Names", "")).split(",")[0],
                immagine=str(e.get("Image", "")),
                stato=str(e.get("State", "")).lower(),
                dettaglio=str(e.get("Status", "")),
                porte=str(e.get("Ports", "")),
                progetto=lab.get("com.docker.compose.project", ""),
                servizio=lab.get("com.docker.compose.service", ""),
            )
        )
    return out


def stato(c: Container) -> str:
    """OK (acceso e sano), AVVIO (in partenza o in riavvio), ERRORE, FERMO (spento senza errori)."""
    if c.stato == "running":
        return {"unhealthy": ERRORE, "starting": AVVIO}.get(c.salute, OK)
    if c.stato == "restarting":
        return AVVIO
    if c.stato == "exited" and c.codice_uscita == 0:
        return FERMO
    if c.stato in ("created", "paused"):
        return FERMO
    return ERRORE


def ordina(elenco: list[Container], progetto: str) -> list[Container]:
    """Prima i container di Metodo v2 (nell'ordine dei servizi), poi gli altri per nome."""

    def chiave(c: Container) -> tuple[int, int, str]:
        if c.progetto == progetto:
            pos = ORDINE_SERVIZI.index(c.servizio) if c.servizio in ORDINE_SERVIZI else len(ORDINE_SERVIZI)
            return (0, pos, c.nome)
        return (1, 0, c.nome)

    return sorted(elenco, key=chiave)


def unisci_statistiche(elenco: list[Container], testo: str) -> None:
    """Aggiunge CPU e memoria dall'uscita di ``docker stats --no-stream --format json``."""
    for riga in (testo or "").splitlines():
        if not riga.strip():
            continue
        try:
            s = json.loads(riga)
        except ValueError:
            continue
        for c in elenco:
            if c.nome == s.get("Name") or str(s.get("ID", "")).startswith(c.id):
                c.cpu, c.memoria = s.get("CPUPerc", ""), str(s.get("MemUsage", "")).split(" / ")[0]


def righe_di_errore(righe: list[str]) -> list[str]:
    return [r for r in righe if any(p in r for p in PAROLE_ERRORE)]


def filtra(righe: list[str], testo: str) -> list[str]:
    t = testo.lower()
    return [r for r in righe if t in r.lower()] if t else righe


def riepilogo(elenco: list[Container], progetto: str) -> str:
    propri = [c for c in elenco if c.progetto == progetto]
    attivi = [c for c in propri if stato(c) == OK]
    errori = [c for c in propri if stato(c) == ERRORE]
    altri = [c for c in elenco if c.progetto != progetto and c.stato == "running"]
    testo = f"Metodo v2: {len(attivi)}/{len(propri)} attivi"
    if errori:
        testo += f", {len(errori)} con errori ({', '.join(c.etichetta for c in errori)}: Invio per il log)"
    if altri:
        # Un container rimasto da una vecchia installazione può rispondere al posto di Metodo v2 sulla stessa porta.
        con_porte = [f"{c.nome} su {porte(c)}" for c in altri if porte(c)]
        testo += f" · altri container accesi: {len(altri)}" + (f" ({', '.join(con_porte)})" if con_porte else "")
    return testo


def porte(c: Container) -> str:
    """«0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp» → «8000→8000»."""
    visti: list[str] = []
    for esterna, interna in re.findall(r":(\d+)->(\d+)/", c.porte):
        t = f"{esterna}→{interna}"
        if t not in visti:
            visti.append(t)
    return " ".join(visti)


# =============================================================================================== docker
def _docker(*argomenti: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *argomenti], capture_output=True, text=True, timeout=timeout)


def progetto_compose(dc: Any) -> str:
    """Nome del progetto Compose di Metodo v2 (dalla configurazione o da ``docker compose config``)."""
    if dc.conf.progetto:
        return dc.conf.progetto
    with contextlib.suppress(Exception):
        r = dc("config", "--format", "json", controlla=False, timeout=30)
        if r.returncode == 0:
            return json.loads(r.stdout).get("name", "")
    return dc.conf.cartella.name.lower()


def log_container(id_: str, righe: int = RIGHE_LOG) -> list[str]:
    r = subprocess.run(
        ["docker", "logs", "--tail", str(righe), id_],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        timeout=30,
    )
    return r.stdout.splitlines()


# =============================================================================================== schermo
class Schermo:
    """Disegno con colori e testo tagliato alla larghezza del terminale (mai un errore di curses)."""

    def __init__(self, scr: Any) -> None:
        self.scr = scr
        self.colori: dict[str, int] = {}
        if curses.has_colors():
            curses.start_color()
            with contextlib.suppress(curses.error):
                curses.use_default_colors()
            for n, (nome, colore) in enumerate(
                (
                    (OK, curses.COLOR_GREEN),
                    (AVVIO, curses.COLOR_YELLOW),
                    (ERRORE, curses.COLOR_RED),
                    (FERMO, curses.COLOR_BLUE),
                    ("titolo", curses.COLOR_CYAN),
                ),
                start=1,
            ):
                curses.init_pair(n, colore, -1)
                self.colori[nome] = curses.color_pair(n)

    def dimensioni(self) -> tuple[int, int]:
        return self.scr.getmaxyx()

    def scrivi(self, y: int, x: int, testo: str, attr: int = 0) -> None:
        alto, largo = self.dimensioni()
        if y < 0 or y >= alto or x >= largo:
            return
        with contextlib.suppress(curses.error):
            self.scr.addnstr(y, x, testo, max(0, largo - x - (1 if y == alto - 1 else 0)), attr)

    def barra(self, y: int, testo: str) -> None:
        _, largo = self.dimensioni()
        self.scrivi(y, 0, testo.ljust(largo), curses.A_REVERSE)

    def colore(self, nome: str) -> int:
        return self.colori.get(nome, 0)


SIMBOLO = {OK: "●", AVVIO: "◐", ERRORE: "✖", FERMO: "○"}

AIUTO = [
    "Pannello di MetodoConsole",
    "",
    "  ↑ ↓  (j k)     scegli un container",
    "  Invio  l       log del container (f segui, e solo errori, / cerca, q indietro)",
    "  a  f  r        avvia · ferma · riavvia il container scelto",
    "  c              console (shell) nel container scelto",
    "",
    "  A  F  R        avvia · ferma · riavvia tutto Metodo v2 (docker compose)",
    "  d              diagnostica completa",
    "  b              backup (database, file e .env)",
    "  u              aggiorna (git pull, backup, ricostruzione, riavvio)",
    "  p              stato e log del prelievo da Metodo",
    "  s              mostra/nascondi CPU e memoria",
    "  o              mostra/nascondi gli altri container del server",
    "  q              esci",
    "",
    "I container in fondo all'elenco non sono di Metodo v2: per esempio quelli di una vecchia installazione",
    "(«dumetodo-…»). Se sono accesi possono occupare le stesse porte: fermali con f.",
    "",
    "Premi un tasto per tornare.",
]


class Pannello:
    def __init__(self, scr: Any, conf: Any, dc: Any, mc: Any) -> None:
        self.s, self.conf, self.dc, self.mc = Schermo(scr), conf, dc, mc
        self.scr = scr
        self.progetto = progetto_compose(dc)
        self.host = socket.gethostname()
        self.versione = mc.versione_codice(conf)
        self.container: list[Container] = []
        self.scelto = 0
        self.inizio = 0
        self.statistiche = False
        self.altri = True
        self.messaggio = ""
        self.errore_docker = ""
        self.letto_il = 0.0
        self._stats_testo = ""
        self._stats_thread: threading.Thread | None = None

    # ------------------------------------------------------------------------------------------- dati
    def aggiorna(self, forza: bool = False) -> None:
        if not forza and time.time() - self.letto_il < AGGIORNA_OGNI:
            return
        self.letto_il = time.time()
        try:
            r = _docker("ps", "--all", "--no-trunc", "--format", "json")
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.errore_docker = f"docker non risponde: {exc}"
            return
        if r.returncode != 0:
            self.errore_docker = (r.stderr or "docker non risponde").strip().splitlines()[-1][:200]
            return
        self.errore_docker = ""
        id_scelto = self.attuale().id if self.attuale() else None
        elenco = ordina(leggi_container(r.stdout), self.progetto)
        if not self.altri:
            elenco = [c for c in elenco if c.progetto == self.progetto]
        if self.statistiche:
            unisci_statistiche(elenco, self._stats_testo)
            self._statistiche_in_background()
        self.container = elenco
        if id_scelto:
            self.scelto = next((i for i, c in enumerate(elenco) if c.id == id_scelto), self.scelto)
        self.scelto = max(0, min(self.scelto, len(elenco) - 1))

    def _statistiche_in_background(self) -> None:
        """``docker stats --no-stream`` impiega qualche secondo: lo si legge in un thread, senza bloccare."""
        if self._stats_thread and self._stats_thread.is_alive():
            return

        def leggi() -> None:
            with contextlib.suppress(Exception):
                self._stats_testo = _docker("stats", "--no-stream", "--format", "json", timeout=60).stdout

        self._stats_thread = threading.Thread(target=leggi, daemon=True)
        self._stats_thread.start()

    def attuale(self) -> Container | None:
        return self.container[self.scelto] if 0 <= self.scelto < len(self.container) else None

    # ------------------------------------------------------------------------------------------- disegno
    def disegna(self) -> None:
        s = self.s
        self.scr.erase()
        alto, largo = s.dimensioni()
        s.barra(0, f" MetodoConsole · Metodo v2 {self.versione} su {self.host} · {self.conf.cartella}")
        s.scrivi(0, max(0, largo - 10), time.strftime(" %H:%M:%S "), curses.A_REVERSE)
        if self.errore_docker:
            s.scrivi(1, 1, self.errore_docker, s.colore(ERRORE) | curses.A_BOLD)
        else:
            s.scrivi(1, 1, riepilogo(self.container, self.progetto))
        elenco = self.mc.elenca_backup(self.conf) if self.conf.backup_cartella.exists() else []
        s.scrivi(2, 1, f"Backup: {len(elenco)}" + (f", ultimo {elenco[0].nome}" if elenco else " (nessuno)"))

        colonne = f"   {'SERVIZIO / CONTAINER':<26} {'STATO':<34} {'PORTE':<28}"
        if self.statistiche:
            colonne += f" {'CPU':>7} {'MEMORIA':>10}"
        s.scrivi(4, 0, colonne, curses.A_BOLD)
        area = max(1, alto - 7)
        if self.scelto < self.inizio:
            self.inizio = self.scelto
        if self.scelto >= self.inizio + area:
            self.inizio = self.scelto - area + 1
        y = 5
        separatore_fatto = False
        for i in range(self.inizio, min(len(self.container), self.inizio + area)):
            c = self.container[i]
            if c.progetto != self.progetto and not separatore_fatto:
                separatore_fatto = True
                s.scrivi(y, 1, "— altri container del server (non di Metodo v2) —", curses.A_DIM)
                y += 1
            st = stato(c)
            nome = c.servizio if c.progetto == self.progetto else c.nome
            riga = f" {SIMBOLO[st]} {nome[:26]:<26} {c.dettaglio[:34]:<34} {porte(c)[:28]:<28}"
            if self.statistiche:
                riga += f" {c.cpu:>7} {c.memoria:>10}"
            attr = s.colore(st)
            if i == self.scelto:
                attr |= curses.A_REVERSE
            s.scrivi(y, 0, riga.ljust(largo) if i == self.scelto else riga, attr)
            y += 1
            if y >= alto - 2:
                break
        if not self.container and not self.errore_docker:
            s.scrivi(6, 3, "Nessun container. Premi A per avviare Metodo v2.")
        if self.messaggio:
            s.scrivi(alto - 2, 1, self.messaggio, curses.A_BOLD)
        s.barra(
            alto - 1,
            " ↑↓ scegli · Invio log · a/f/r avvia/ferma/riavvia · A/F/R tutto · d diagnostica · b backup"
            " · ? aiuto · q esci",
        )
        self.scr.refresh()

    # ------------------------------------------------------------------------------------------- dialoghi
    def conferma(self, domanda: str) -> bool:
        alto, largo = self.s.dimensioni()
        testo = f" {domanda} [s/N] "
        y, x = alto // 2, max(0, (largo - len(testo)) // 2)
        self.s.scrivi(y - 1, x, " " * len(testo), curses.A_REVERSE)
        self.s.scrivi(y, x, testo, curses.A_REVERSE | curses.A_BOLD)
        self.s.scrivi(y + 1, x, " " * len(testo), curses.A_REVERSE)
        self.scr.timeout(-1)
        tasto = self.scr.getch()
        self.scr.timeout(500)
        return tasto in (ord("s"), ord("S"), ord("y"), ord("Y"))

    def chiedi(self, domanda: str) -> str:
        alto, _ = self.s.dimensioni()
        self.s.scrivi(alto - 2, 0, " " * 200)
        self.s.scrivi(alto - 2, 1, domanda, curses.A_BOLD)
        curses.echo()
        curses.curs_set(1)
        self.scr.timeout(-1)
        try:
            valore = self.scr.getstr(alto - 2, len(domanda) + 2, 60).decode(errors="replace")
        except curses.error:
            valore = ""
        finally:
            curses.noecho()
            with contextlib.suppress(curses.error):
                curses.curs_set(0)
            self.scr.timeout(500)
        return valore.strip()

    def nel_terminale(self, titolo: str, funzione: Any) -> None:
        """Esce dal pannello, esegue l'operazione con l'output normale di docker e torna dopo Invio."""
        curses.def_prog_mode()
        curses.endwin()
        print(f"\n=== {titolo} ===\n", flush=True)
        esito = None
        try:
            esito = funzione()
        except self.mc.Errore as exc:
            print(f"\nErrore: {exc}")
        except KeyboardInterrupt:
            print("\nInterrotto.")
        if isinstance(esito, int) and esito not in (0, None):
            print(f"\nTerminato con codice {esito}.")
        with contextlib.suppress(EOFError, KeyboardInterrupt):
            input("\nPremi Invio per tornare al pannello… ")
        curses.reset_prog_mode()
        self.scr.refresh()
        self.aggiorna(forza=True)

    def testo_scorrevole(self, titolo: str, carica: Any, segui_possibile: bool = False) -> None:
        """Visualizzatore: frecce, PagSu/PagGiù, g/G inizio/fine, f segui, e solo errori, / cerca, q indietro."""
        righe = carica()
        segui, solo_errori, cerca = segui_possibile, False, ""
        inizio = None  # None = in fondo
        ultimo = time.time()
        while True:
            if segui and time.time() - ultimo > 2:
                righe, ultimo = carica(), time.time()
            vista = filtra(righe_di_errore(righe) if solo_errori else righe, cerca)
            alto, _ = self.s.dimensioni()
            area = max(1, alto - 2)
            massimo = max(0, len(vista) - area)
            pos = massimo if inizio is None else max(0, min(inizio, massimo))
            self.scr.erase()
            stato_vista = " · ".join(
                x
                for x in (
                    "segue" if segui else "",
                    "solo errori" if solo_errori else "",
                    f"cerca «{cerca}»" if cerca else "",
                    f"righe {pos + 1}-{min(len(vista), pos + area)} di {len(vista)}",
                )
                if x
            )
            self.s.barra(0, f" {titolo} · {stato_vista}")
            for n, riga in enumerate(vista[pos : pos + area]):
                attr = self.s.colore(ERRORE) if any(p in riga for p in PAROLE_ERRORE) else 0
                self.s.scrivi(1 + n, 0, riga.replace("\t", "    "), attr)
            aiuto = " ↑↓ PagSu/PagGiù · g/G inizio/fine · e solo errori · / cerca · q indietro"
            self.s.barra(alto - 1, aiuto + (" · f segui" if segui_possibile else ""))
            self.scr.refresh()
            tasto = self.scr.getch()
            if tasto in (ord("q"), 27, curses.KEY_LEFT):
                return
            if tasto in (curses.KEY_UP, ord("k")):
                inizio, segui = max(0, pos - 1), False
            elif tasto in (curses.KEY_DOWN, ord("j")):
                inizio = None if pos + 1 >= massimo else pos + 1
            elif tasto == curses.KEY_PPAGE:
                inizio, segui = max(0, pos - area), False
            elif tasto in (curses.KEY_NPAGE, ord(" ")):
                inizio = None if pos + area >= massimo else pos + area
            elif tasto in (ord("g"), curses.KEY_HOME):
                inizio, segui = 0, False
            elif tasto in (ord("G"), curses.KEY_END):
                inizio = None
            elif tasto == ord("f") and segui_possibile:
                segui, inizio = not segui, None
                righe = carica()
            elif tasto == ord("e"):
                solo_errori, inizio = not solo_errori, None
            elif tasto == ord("/"):
                cerca, inizio = self.chiedi("Cerca (vuoto = tutto):"), None
            elif tasto == curses.KEY_RESIZE:
                continue

    # ------------------------------------------------------------------------------------------- azioni
    def azione_container(self, verbo: str) -> None:
        c = self.attuale()
        if not c:
            return
        nomi = {"start": "Avviare", "stop": "Fermare", "restart": "Riavviare"}
        if verbo != "start" and not self.conferma(f"{nomi[verbo]} {c.nome}?"):
            self.messaggio = "Annullato."
            return
        self.messaggio = f"{nomi[verbo]} {c.nome}…"
        self.disegna()
        r = _docker(verbo, c.id, timeout=180)
        fatto = {"start": "avviato", "stop": "fermato", "restart": "riavviato"}[verbo]
        self.messaggio = f"{c.nome} {fatto}." if r.returncode == 0 else f"Errore: {(r.stderr or '').strip()[:200]}"
        self.aggiorna(forza=True)

    def diagnostica(self) -> None:
        self.messaggio = "Diagnostica in corso…"
        self.disegna()
        esiti = self.mc.diagnostica_completa(self.conf, self.dc)
        etichette = self.mc.ETICHETTA
        righe = [f"{etichette[e.esito]}  {e.nome:<30} {e.dettaglio}" for e in esiti]
        righe += ["", f"Esito: {self.mc.peggiore(esiti).upper()}"]
        self.messaggio = ""
        self.testo_scorrevole("Diagnostica", lambda: righe)

    def prelievo(self) -> None:
        nome = self.mc.nome_prelievo(self.conf)
        esito = self.mc.descrivi_prelievo(self.mc.stato_container(nome))
        if esito is None:
            self.messaggio = "Nessun prelievo avviato (metodoconsole prelievo avvia)."
            return
        self.testo_scorrevole(f"Prelievo da Metodo: {esito.dettaglio}", lambda: log_container(nome), True)

    def esegui(self) -> None:
        with contextlib.suppress(curses.error):
            curses.curs_set(0)
        self.scr.timeout(500)
        self.scr.keypad(True)
        self.aggiorna(forza=True)
        ns = self.mc.argparse.Namespace
        while True:
            self.aggiorna()
            self.disegna()
            tasto = self.scr.getch()
            if tasto == -1:
                continue
            self.messaggio = ""
            c = self.attuale()
            if tasto in (ord("q"), 27):
                return
            if tasto in (curses.KEY_UP, ord("k")):
                self.scelto = max(0, self.scelto - 1)
            elif tasto in (curses.KEY_DOWN, ord("j")):
                self.scelto = min(len(self.container) - 1, self.scelto + 1)
            elif tasto == curses.KEY_PPAGE:
                self.scelto = max(0, self.scelto - 10)
            elif tasto == curses.KEY_NPAGE:
                self.scelto = min(len(self.container) - 1, self.scelto + 10)
            elif tasto in (10, 13, curses.KEY_ENTER, ord("l"), curses.KEY_RIGHT) and c:
                self.testo_scorrevole(f"Log di {c.nome}", lambda c=c: log_container(c.id), segui_possibile=True)
            elif tasto == ord("a"):
                self.azione_container("start")
            elif tasto == ord("f"):
                self.azione_container("stop")
            elif tasto == ord("r"):
                self.azione_container("restart")
            elif tasto == ord("c") and c:
                if c.stato != "running":
                    self.messaggio = "Il container è fermo: avvialo prima (a)."
                else:
                    self.nel_terminale(
                        f"Console in {c.nome} (exit per uscire)",
                        lambda c=c: (
                            subprocess.run(
                                [
                                    "docker",
                                    "exec",
                                    "-it",
                                    c.id,
                                    "sh",
                                    "-c",
                                    "command -v bash >/dev/null && exec bash || exec sh",
                                ]
                            ).returncode
                        ),
                    )
            elif tasto == ord("A"):
                self.nel_terminale(
                    "Avvio di Metodo v2",
                    lambda: self.mc.cmd_avvia(self.conf, self.dc, ns(ricostruisci=False, servizi=[])),
                )
            elif tasto == ord("F"):
                if self.conferma("Fermare tutto Metodo v2? Gli utenti non potranno lavorare."):
                    self.nel_terminale(
                        "Arresto di Metodo v2", lambda: self.mc.cmd_ferma(self.conf, self.dc, ns(servizi=[]))
                    )
            elif tasto == ord("R"):
                if self.conferma("Riavviare tutto Metodo v2?"):
                    self.nel_terminale(
                        "Riavvio di Metodo v2", lambda: self.mc.cmd_riavvia(self.conf, self.dc, ns(servizi=[]))
                    )
            elif tasto == ord("d"):
                self.diagnostica()
            elif tasto == ord("b"):
                if self.conferma("Fare un backup adesso?"):
                    nota = self.chiedi("Nota del backup (facoltativa):")
                    self.nel_terminale(
                        "Backup",
                        lambda nota=nota: self.mc.cmd_backup(
                            self.conf, self.dc, ns(nota=nota or None, solo_database=False, avvisa=False)
                        ),
                    )
            elif tasto == ord("u"):
                if self.conferma("Aggiornare Metodo v2 (git pull, backup, ricostruzione, riavvio)?"):
                    self.nel_terminale(
                        "Aggiornamento",
                        lambda: self.mc.cmd_aggiorna(self.conf, self.dc, ns(senza_backup=False, forza=False)),
                    )
            elif tasto == ord("p"):
                self.prelievo()
            elif tasto == ord("s"):
                self.statistiche = not self.statistiche
                self.messaggio = "CPU e memoria: si aggiornano in pochi secondi." if self.statistiche else ""
                self.aggiorna(forza=True)
            elif tasto == ord("o"):
                self.altri = not self.altri
                self.aggiorna(forza=True)
            elif tasto == ord("?"):
                self.testo_scorrevole("Aiuto", lambda: AIUTO)
            elif tasto == curses.KEY_RESIZE:
                continue


def avvia(conf: Any, dc: Any, mc: Any) -> int:
    """Punto d'ingresso: ``metodoconsole pannello``."""
    import sys

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise mc.Errore("Il pannello va aperto da un terminale (anche via SSH: ssh -t server metodoconsole pannello).")
    locale.setlocale(locale.LC_ALL, "")  # caratteri accentati e simboli in UTF-8

    def principale(scr: Any) -> None:
        with contextlib.suppress(curses.error):
            curses.curs_set(0)
        Pannello(scr, conf, dc, mc).esegui()

    with contextlib.suppress(KeyboardInterrupt):
        curses.wrapper(principale)
    return 0
