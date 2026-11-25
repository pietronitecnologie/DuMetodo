"""Letture del server per l'interfaccia di MetodoConsole: Docker, risorse, dischi, firewall, antivirus, accessi,
aggiornamenti, porte in ascolto e i controlli di sicurezza.

Nessuna dipendenza da Textual: sono funzioni semplici (i «leggi_*» trasformano il testo dei comandi in dati),
provate dai test senza Docker né root. Ogni comando assente o non permesso dà un risultato vuoto, mai un errore:
l'interfaccia mostra «non installato» o «serve root».
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RIGHE_LOG = 1000
ORDINE_SERVIZI = ("db", "redis", "migrate", "web", "worker", "beat")
PAROLE_ERRORE = ("Traceback", " ERROR", "CRITICAL", "Error:", "Exception", "FATAL", "panic:")
TIMER = ("metodoconsole-backup", "metodoconsole-controllo")
LOG_SCANSIONE = Path("/var/log/metodoconsole/scansione-antivirus.log")

# Livelli (anche nomi dei colori): stato dei container e gravità dei controlli.
OK, AVVIO, ERRORE, FERMO, ATTENZIONE = "ok", "avvio", "errore", "fermo", "attenzione"


def esegui(cmd: list[str], timeout: float = 30) -> tuple[int, str]:
    """(codice, uscita) di un comando; 127 se non esiste. Mai un'eccezione."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
    except FileNotFoundError:
        return 127, ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return r.returncode, (r.stdout or "") + (r.stderr if r.returncode else "")


def presente(comando: str) -> bool:
    return shutil.which(comando) is not None


def root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def misura(n: float) -> str:
    for unita in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unita == "TB":
            return f"{n:.0f} {unita}" if unita in ("B", "KB") else f"{n:.1f} {unita}"
        n /= 1024
    return str(n)


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
def docker(*argomenti: str, timeout: float = 30) -> tuple[int, str]:
    return esegui(["docker", *argomenti], timeout=timeout)


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


# =============================================================================================== sistema
def leggi_meminfo(testo: str) -> dict[str, int]:
    """/proc/meminfo → {chiave: byte}."""
    out: dict[str, int] = {}
    for riga in testo.splitlines():
        chiave, _, valore = riga.partition(":")
        parti = valore.split()
        if parti and parti[0].isdigit():
            out[chiave.strip()] = int(parti[0]) * (1024 if len(parti) > 1 and parti[1].lower() == "kb" else 1)
    return out


@dataclass
class Risorse:
    carico: tuple[float, float, float] = (0.0, 0.0, 0.0)
    cpu: int = 1
    memoria_totale: int = 0
    memoria_disponibile: int = 0
    swap_totale: int = 0
    swap_libero: int = 0
    acceso_da: float = 0.0  # secondi dall'avvio del server

    @property
    def memoria_usata(self) -> float:
        return 1 - self.memoria_disponibile / self.memoria_totale if self.memoria_totale else 0.0

    @property
    def swap_usato(self) -> float:
        return 1 - self.swap_libero / self.swap_totale if self.swap_totale else 0.0


def leggi_risorse() -> Risorse:
    r = Risorse(cpu=os.cpu_count() or 1)
    with contextlib.suppress(OSError, AttributeError):
        r.carico = os.getloadavg()
    with contextlib.suppress(OSError):
        m = leggi_meminfo(Path("/proc/meminfo").read_text())
        r.memoria_totale = m.get("MemTotal", 0)
        r.memoria_disponibile = m.get("MemAvailable", m.get("MemFree", 0))
        r.swap_totale, r.swap_libero = m.get("SwapTotal", 0), m.get("SwapFree", 0)
    with contextlib.suppress(OSError, ValueError, IndexError):
        r.acceso_da = float(Path("/proc/uptime").read_text().split()[0])
    return r


@dataclass
class Disco:
    etichetta: str
    percorso: str
    totale: int
    usato: int

    @property
    def frazione(self) -> float:
        return self.usato / self.totale if self.totale else 0.0

    @property
    def libero(self) -> int:
        return self.totale - self.usato


def leggi_dischi(percorsi: list[tuple[str, str]]) -> list[Disco]:
    """Uso dei dischi; i percorsi sullo stesso filesystem compaiono una volta sola (con le etichette unite)."""
    per_device: dict[int, Disco] = {}
    for etichetta, percorso in percorsi:
        p = Path(percorso)
        while not p.exists() and p != p.parent:
            p = p.parent
        try:
            dev, u = p.stat().st_dev, shutil.disk_usage(p)
        except OSError:
            continue
        if dev in per_device:
            per_device[dev].etichetta += f", {etichetta}"
        else:
            per_device[dev] = Disco(etichetta, str(p), u.total, u.total - u.free)
    return list(per_device.values())


def sistema_operativo() -> str:
    with contextlib.suppress(OSError):
        for riga in Path("/etc/os-release").read_text().splitlines():
            if riga.startswith("PRETTY_NAME="):
                return riga.split("=", 1)[1].strip().strip('"')
    return platform.platform()


def barra(frazione: float, larghezza: int = 20) -> str:
    frazione = max(0.0, min(1.0, frazione))
    pieni = round(frazione * larghezza)
    return "█" * pieni + "░" * (larghezza - pieni)


def durata(secondi: float) -> str:
    secondi = int(secondi)
    giorni, resto = divmod(secondi, 86400)
    ore, resto = divmod(resto, 3600)
    minuti = resto // 60
    if giorni:
        return f"{giorni} g {ore} h"
    if ore:
        return f"{ore} h {minuti} min"
    return f"{minuti} min"


def leggi_systemd(testo: str) -> dict[str, str]:
    """Uscita di ``systemctl show -p A -p B`` (righe chiave=valore)."""
    out = {}
    for riga in testo.splitlines():
        chiave, sep, valore = riga.partition("=")
        if sep:
            out[chiave.strip()] = valore.strip()
    return out


@dataclass
class Timer:
    nome: str
    attivo: bool
    prossimo: str = ""
    ultimo: str = ""
    esito: str = ""  # esito dell'ultima esecuzione del servizio: success, exit-code, …


def _data_systemd(testo: str) -> str:
    """«Wed 2026-11-25 02:30:00 CET» → «25/11 02:30»; «n/a» o vuoto → «»."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})", testo or "")
    return f"{m.group(3)}/{m.group(2)} {m.group(4)}:{m.group(5)}" if m else ""


def stato_timer(nome: str) -> Timer | None:
    """Stato del timer di systemd (None se systemd non c'è o il timer non è installato)."""
    if not shutil.which("systemctl"):
        return None
    try:
        t = leggi_systemd(
            subprocess.run(
                [
                    "systemctl",
                    "show",
                    f"{nome}.timer",
                    "-p",
                    "LoadState",
                    "-p",
                    "ActiveState",
                    "-p",
                    "NextElapseUSecRealtime",
                    "-p",
                    "LastTriggerUSec",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        )
        s = leggi_systemd(
            subprocess.run(
                ["systemctl", "show", f"{nome}.service", "-p", "Result"], capture_output=True, text=True, timeout=10
            ).stdout
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if t.get("LoadState") in (None, "not-found"):
        return None
    return Timer(
        nome,
        t.get("ActiveState") == "active",
        _data_systemd(t.get("NextElapseUSecRealtime", "")),
        _data_systemd(t.get("LastTriggerUSec", "")),
        s.get("Result", ""),
    )


def leggi_df(testo: str) -> list[dict[str, str]]:
    """Uscita di ``docker system df --format json`` (una riga JSON per tipo: immagini, container, volumi, cache)."""
    out = []
    for riga in (testo or "").splitlines():
        with contextlib.suppress(ValueError):
            out.append(json.loads(riga))
    return out


def avvisi(
    risorse: Risorse,
    dischi: list[Disco],
    elenco: list[Container],
    progetto: str,
    backup_eta_ore: float | None,
    massima_eta_ore: int,
    timer: list[Timer],
    timer_attesi: tuple[str, ...] = TIMER,
) -> list[tuple[str, str]]:
    """I problemi da mostrare in Panoramica, ognuno con cosa fare: [(livello, testo)]."""
    out: list[tuple[str, str]] = []
    for d in dischi:
        if d.frazione >= 0.9:
            out.append((ERRORE, f"Disco quasi pieno ({d.etichetta}, {d.frazione:.0%}): libera spazio (scheda Disco)"))
        elif d.frazione >= 0.8:
            out.append((ATTENZIONE, f"Disco pieno all'{d.frazione:.0%} ({d.etichetta}): tienilo d'occhio"))
    if risorse.memoria_totale and risorse.memoria_usata >= 0.9:
        out.append((ATTENZIONE, f"Memoria quasi esaurita ({risorse.memoria_usata:.0%}): guarda i container"))
    if risorse.carico[1] > risorse.cpu * 1.5:
        out.append((ATTENZIONE, f"Server molto carico: {risorse.carico[1]:.1f} su {risorse.cpu} CPU (media 5 minuti)"))
    propri = [c for c in elenco if c.progetto == progetto]
    if not propri:
        out.append((ERRORE, "Metodo v2 non è avviato: premi A per avviarlo"))
    for c in propri:
        if stato(c) == ERRORE:
            out.append((ERRORE, f"Servizio {c.etichetta}: {c.dettaglio} (scheda Docker → log)"))
    for c in elenco:
        if c.progetto != progetto and c.stato == "running" and porte(c):
            out.append(
                (
                    ATTENZIONE,
                    f"{c.nome} (non di Metodo v2) è acceso sulla porta {porte(c)}: fermalo (scheda Docker)",
                )
            )
    if backup_eta_ore is None:
        out.append((ERRORE, "Nessun backup: premi b per farne uno"))
    elif backup_eta_ore > massima_eta_ore:
        out.append((ERRORE, f"Ultimo backup di {backup_eta_ore:.0f} ore fa: controlla il backup automatico"))
    trovati = {t.nome: t for t in timer}
    for nome in timer_attesi:
        t = trovati.get(nome)
        if t is None:
            out.append((ATTENZIONE, f"Controllo automatico {nome} non installato: sudo ./MetodoConsole/installa.sh"))
        elif not t.attivo:
            out.append((ATTENZIONE, f"Controllo automatico {nome} spento: sudo systemctl enable --now {nome}.timer"))
        elif t.esito and t.esito != "success":
            out.append((ATTENZIONE, f"Ultima esecuzione di {nome} non riuscita ({t.esito}): scheda Backup"))
    return out or [(OK, "Nessun problema rilevato.")]


# ============================================================================================== firewall
@dataclass
class Firewall:
    strumento: str = ""  # ufw, firewalld, nftables, iptables; "" = nessuno trovato
    attivo: bool = False
    regole: list[str] = field(default_factory=list)
    gestibile: bool = False  # le azioni del pannello funzionano solo con ufw
    nota: str = ""


def leggi_ufw(testo: str) -> tuple[bool, list[str]]:
    """``ufw status numbered`` → (attivo, regole «[ 1] 22/tcp ALLOW IN Anywhere»)."""
    attivo = bool(re.search(r"Status:\s*active", testo or ""))
    regole = [r.strip() for r in (testo or "").splitlines() if re.match(r"\s*\[\s*\d+\]", r)]
    return attivo, regole


def leggi_nft(testo: str) -> tuple[bool, list[str]]:
    """``nft list ruleset``: attivo se c'è una catena di input che scarta per regola predefinita o con regole."""
    righe = [r.strip() for r in (testo or "").splitlines() if r.strip()]
    filtro = any("hook input" in r and ("policy drop" in r or "policy reject" in r) for r in righe)
    regole = [r for r in righe if not r.startswith(("table", "chain", "}", "type"))]
    return filtro, regole


def firewall() -> Firewall:
    if presente("ufw"):
        codice, testo = esegui(["ufw", "status", "numbered"])
        if codice == 0:
            attivo, regole = leggi_ufw(testo)
            return Firewall("ufw", attivo, regole, gestibile=True)
        return Firewall("ufw", nota="serve root per leggere lo stato (sudo metodoconsole)")
    if presente("firewall-cmd"):
        codice, stato_fw = esegui(["firewall-cmd", "--state"])
        _, regole = esegui(["firewall-cmd", "--list-all"])
        return Firewall("firewalld", codice == 0 and "running" in stato_fw, regole.splitlines())
    if presente("nft"):
        codice, testo = esegui(["nft", "list", "ruleset"])
        if codice == 0:
            attivo, regole = leggi_nft(testo)
            return Firewall("nftables", attivo, regole)
        return Firewall("nftables", nota="serve root per leggere le regole")
    if presente("iptables"):
        codice, testo = esegui(["iptables", "-S", "INPUT"])
        if codice == 0:
            righe = testo.splitlines()
            return Firewall("iptables", any(r.startswith("-P INPUT DROP") for r in righe) or len(righe) > 1, righe)
    return Firewall(nota="nessun firewall installato: apt install ufw")


# ============================================================================================== antivirus
@dataclass
class Antivirus:
    installato: bool = False
    versione: str = ""
    firme_del: str = ""  # data delle firme, come la scrive ClamAV
    firme_giorni: int | None = None  # età delle firme in giorni
    demone: bool = False  # clamav-daemon attivo (scansioni più veloci)
    aggiornamento: bool = False  # clamav-freshclam attivo (firme aggiornate da sole)
    ultima_scansione: dict[str, str] = field(default_factory=dict)
    scansione_in_corso: bool = False


def leggi_versione_clamav(testo: str) -> tuple[str, str]:
    """«ClamAV 1.0.7/27470/Mon Nov 24 09:35:02 2025» → («1.0.7», «Mon Nov 24 09:35:02 2025»)."""
    m = re.match(r"ClamAV\s+([^/\s]+)(?:/\d+/(.+))?", (testo or "").strip())
    return (m.group(1), (m.group(2) or "").strip()) if m else ("", "")


def eta_firme(data: str, oggi: Any = None) -> int | None:
    import datetime as dt

    with contextlib.suppress(ValueError):
        quando = dt.datetime.strptime(data, "%a %b %d %H:%M:%S %Y")
        return ((oggi or dt.datetime.now()) - quando).days
    return None


def leggi_riepilogo_scansione(testo: str) -> dict[str, str]:
    """La parte «SCAN SUMMARY» del log di clamscan: {"Infected files": "0", "Scanned files": "123", …}."""
    parte = (testo or "").rsplit("SCAN SUMMARY", 1)
    if len(parte) < 2:
        return {}
    out = {}
    for riga in parte[1].splitlines():
        chiave, sep, valore = riga.partition(":")
        if sep and chiave.strip():
            out[chiave.strip()] = valore.strip()
    infetti = [r.strip() for r in parte[0].splitlines() if r.rstrip().endswith("FOUND")]
    if infetti:
        out["trovati"] = "\n".join(infetti[-20:])
    return out


def servizio_attivo(nome: str) -> bool:
    return presente("systemctl") and esegui(["systemctl", "is-active", "--quiet", nome])[0] == 0


def antivirus() -> Antivirus:
    a = Antivirus(installato=presente("clamscan"))
    if not a.installato:
        return a
    a.versione, a.firme_del = leggi_versione_clamav(esegui(["clamscan", "--version"])[1])
    a.firme_giorni = eta_firme(a.firme_del)
    a.demone = servizio_attivo("clamav-daemon")
    a.aggiornamento = servizio_attivo("clamav-freshclam")
    with contextlib.suppress(OSError):
        if LOG_SCANSIONE.exists():
            a.ultima_scansione = leggi_riepilogo_scansione(LOG_SCANSIONE.read_text(errors="replace"))
            a.ultima_scansione.setdefault("_file", str(LOG_SCANSIONE))
    a.scansione_in_corso = esegui(["pgrep", "-f", f"clamscan.*{LOG_SCANSIONE.name}"])[0] == 0
    return a


def comando_scansione(percorsi: list[str]) -> list[str]:
    """clamscan ricorsivo, solo i file infetti nel log; nessuna rimozione automatica (si decide dopo)."""
    esistenti = [p for p in percorsi if Path(p).exists()]
    return ["nice", "-n", "15", "clamscan", "-r", "-i", "--log", str(LOG_SCANSIONE), *esistenti]


def avvia_scansione(percorsi: list[str]) -> str:
    """Scansione in background (continua anche chiudendo il pannello); il risultato finisce nel log."""
    LOG_SCANSIONE.parent.mkdir(parents=True, exist_ok=True)
    LOG_SCANSIONE.write_text("")
    subprocess.Popen(
        comando_scansione(percorsi),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return str(LOG_SCANSIONE)


# ============================================================================================== accessi
@dataclass
class Accessi:
    ssh: dict[str, str] = field(default_factory=dict)  # impostazioni effettive di sshd (sshd -T)
    fail2ban: bool = False
    bannati: dict[str, list[str]] = field(default_factory=dict)  # jail → IP bloccati
    tentativi_24h: int = 0
    ip_tentativi: list[tuple[str, int]] = field(default_factory=list)
    sudo: list[str] = field(default_factory=list)
    ultimi: list[str] = field(default_factory=list)


def leggi_sshd(testo: str) -> dict[str, str]:
    """``sshd -T`` (configurazione effettiva) → {"permitrootlogin": "no", "port": "22", …}."""
    out: dict[str, str] = {}
    for riga in (testo or "").splitlines():
        chiave, _, valore = riga.strip().partition(" ")
        if chiave and chiave.lower() not in out:
            out[chiave.lower()] = valore.strip()
    return out


def leggi_fail2ban_jail(testo: str) -> list[str]:
    m = re.search(r"Banned IP list:\s*(.*)", testo or "")
    return m.group(1).split() if m else []


def leggi_jail(testo: str) -> list[str]:
    m = re.search(r"Jail list:\s*(.*)", testo or "")
    return [j.strip() for j in m.group(1).split(",") if j.strip()] if m else []


def conta_tentativi(testo: str) -> tuple[int, list[tuple[str, int]]]:
    """Accessi SSH falliti dal registro di sistema: totale e IP più frequenti."""
    ip: Counter[str] = Counter()
    totale = 0
    for riga in (testo or "").splitlines():
        if "Failed password" in riga or "Invalid user" in riga or "authentication failure" in riga:
            totale += 1
            m = re.search(r"from (\S+)", riga) or re.search(r"rhost=(\S+)", riga)
            if m:
                ip[m.group(1)] += 1
    return totale, ip.most_common(10)


def accessi() -> Accessi:
    a = Accessi()
    if presente("sshd"):
        codice, testo = esegui(["sshd", "-T"])
        if codice == 0:
            a.ssh = leggi_sshd(testo)
    a.fail2ban = presente("fail2ban-client") and servizio_attivo("fail2ban")
    if a.fail2ban:
        for jail in leggi_jail(esegui(["fail2ban-client", "status"])[1]):
            a.bannati[jail] = leggi_fail2ban_jail(esegui(["fail2ban-client", "status", jail])[1])
    if presente("journalctl"):
        testo = esegui(
            ["journalctl", "--since", "24 hours ago", "--no-pager", "-q", "-u", "ssh", "-u", "sshd", "-o", "cat"],
            timeout=60,
        )[1]
        a.tentativi_24h, a.ip_tentativi = conta_tentativi(testo)
    codice, testo = esegui(["getent", "group", "sudo"])
    if codice == 0 and testo.strip():
        a.sudo = [u for u in testo.strip().split(":")[-1].split(",") if u]
    codice, testo = esegui(["last", "-n", "8", "-w", "--time-format", "iso"])
    a.ultimi = [r for r in testo.splitlines() if r.strip() and not r.startswith("wtmp")][:8]
    return a


# ============================================================================================== aggiornamenti
@dataclass
class Aggiornamenti:
    pacchetti: list[tuple[str, str, bool]] = field(default_factory=list)  # (nome, versione, di sicurezza)
    riavvio_richiesto: bool = False
    riavvio_pacchetti: list[str] = field(default_factory=list)
    automatici: bool = False  # unattended-upgrades installato e attivo
    disponibile: bool = True  # apt presente

    @property
    def sicurezza(self) -> int:
        return sum(1 for p in self.pacchetti if p[2])


def leggi_apt_aggiornabili(testo: str) -> list[tuple[str, str, bool]]:
    """``apt list --upgradable`` → [(nome, versione, di sicurezza)]."""
    out = []
    for riga in (testo or "").splitlines():
        m = re.match(r"([^/\s]+)/(\S+)\s+(\S+)", riga)
        if m and "upgradable" in riga:
            out.append((m.group(1), m.group(3), "security" in m.group(2)))
    return out


def aggiornamenti() -> Aggiornamenti:
    a = Aggiornamenti(disponibile=presente("apt"))
    if a.disponibile:
        a.pacchetti = leggi_apt_aggiornabili(esegui(["apt", "list", "--upgradable"], timeout=60)[1])
    a.riavvio_richiesto = Path("/var/run/reboot-required").exists()
    with contextlib.suppress(OSError):
        a.riavvio_pacchetti = Path("/var/run/reboot-required.pkgs").read_text().split()
    with contextlib.suppress(OSError):
        conf = Path("/etc/apt/apt.conf.d/20auto-upgrades").read_text()
        a.automatici = presente("unattended-upgrade") and bool(re.search(r'Unattended-Upgrade\s+"1"', conf))
    return a


# ============================================================================================== porte
@dataclass
class Porta:
    protocollo: str
    indirizzo: str
    porta: int
    processo: str

    @property
    def pubblica(self) -> bool:
        return self.indirizzo not in ("127.0.0.1", "::1", "localhost") and not self.indirizzo.startswith("127.")


def leggi_ss(testo: str) -> list[Porta]:
    """``ss -tulpnH`` → porte in ascolto (una per indirizzo e porta)."""
    out, visti = [], set()
    for riga in (testo or "").splitlines():
        parti = riga.split()
        if len(parti) < 5:
            continue
        locale_ = parti[4]
        indirizzo, _, porta = locale_.rpartition(":")
        indirizzo = indirizzo.strip("[]").split("%")[0] or "*"
        if not porta.isdigit():
            continue
        m = re.search(r'users:\(\("([^"]+)"', riga)
        chiave = (parti[0], indirizzo, porta)
        if chiave in visti:
            continue
        visti.add(chiave)
        out.append(Porta(parti[0], indirizzo, int(porta), m.group(1) if m else ""))
    return sorted(out, key=lambda p: (p.porta, p.protocollo))


def porte_in_ascolto() -> list[Porta]:
    return leggi_ss(esegui(["ss", "-tulpnH"])[1]) if presente("ss") else []


# ============================================================================================== spazio
def leggi_du(testo: str) -> list[tuple[int, str]]:
    """``du -x -d1 -B1 PERCORSO`` → [(byte, cartella)] dalla più grande, senza la riga del percorso stesso."""
    righe = []
    for riga in (testo or "").splitlines():
        dimensione, _, percorso = riga.partition("\t")
        if dimensione.strip().isdigit():
            righe.append((int(dimensione), percorso.strip()))
    if righe:
        radice = min(righe, key=lambda r: len(r[1]))[1]
        righe = [r for r in righe if r[1] != radice]
    return sorted(righe, reverse=True)


def cartelle_grandi(percorso: str, quante: int = 15) -> list[tuple[int, str]]:
    return leggi_du(esegui(["du", "-x", "-d1", "-B1", percorso], timeout=300)[1])[:quante]


def leggi_journal_spazio(testo: str) -> str:
    m = re.search(r"take up ([\d.,]+\s*[KMGT]?)B?", testo or "")
    return (m.group(1).replace(" ", "") + "B") if m else ""


def spazio_journal() -> str:
    return leggi_journal_spazio(esegui(["journalctl", "--disk-usage"])[1]) if presente("journalctl") else ""


# ============================================================================================== sicurezza
@dataclass
class Controllo:
    livello: str  # OK, ATTENZIONE, ERRORE
    nome: str
    dettaglio: str
    rimedio: str = ""


PORTE_ATTESE = {22, 80, 443}


def controlli_sicurezza(
    fw: Firewall,
    av: Antivirus,
    ac: Accessi,
    ag: Aggiornamenti,
    porte: list[Porta],
    env_permessi: int | None = None,
    pubblicate_docker: list[str] | None = None,
) -> list[Controllo]:
    """Il riepilogo della scheda Sicurezza: ogni controllo con cosa fare se non va."""
    c: list[Controllo] = []
    add = c.append
    # firewall
    if not fw.strumento:
        add(Controllo(ERRORE, "Firewall", "nessun firewall installato", "Sicurezza → Firewall → Installa ufw"))
    elif fw.nota:
        add(Controllo(ATTENZIONE, "Firewall", f"{fw.strumento}: {fw.nota}"))
    elif not fw.attivo:
        add(Controllo(ERRORE, "Firewall", f"{fw.strumento} installato ma spento", "Sicurezza → Firewall → Attiva"))
    else:
        add(Controllo(OK, "Firewall", f"{fw.strumento} attivo, {len(fw.regole)} regole"))
    if pubblicate_docker and fw.strumento == "ufw" and fw.attivo:
        add(
            Controllo(
                ATTENZIONE,
                "Porte di Docker",
                f"Docker apre {', '.join(pubblicate_docker)} senza passare da ufw",
                "pubblica le porte su 127.0.0.1 dietro un proxy, o limita l'accesso nella rete",
            )
        )
    # accessi SSH
    ssh = ac.ssh
    if ssh:
        if ssh.get("permitrootlogin", "") in ("yes",):
            add(
                Controllo(
                    ERRORE,
                    "SSH: accesso di root",
                    "root può entrare con la password",
                    "PermitRootLogin prohibit-password (o no) in /etc/ssh/sshd_config",
                )
            )
        else:
            add(Controllo(OK, "SSH: accesso di root", ssh.get("permitrootlogin", "")))
        if ssh.get("passwordauthentication") == "yes":
            add(
                Controllo(
                    ATTENZIONE,
                    "SSH: password",
                    "accesso con password abilitato",
                    "usa le chiavi SSH e imposta PasswordAuthentication no",
                )
            )
        else:
            add(Controllo(OK, "SSH: password", "solo chiavi"))
    else:
        add(Controllo(ATTENZIONE, "SSH", "configurazione non leggibile (serve root) o sshd assente"))
    if ac.fail2ban:
        bannati = sum(len(v) for v in ac.bannati.values())
        add(Controllo(OK, "Fail2ban", f"attivo ({', '.join(ac.bannati) or 'nessuna jail'}), {bannati} IP bloccati"))
    else:
        add(
            Controllo(
                ATTENZIONE,
                "Fail2ban",
                "non attivo: nessun blocco dei tentativi di accesso",
                "Sicurezza → Accessi → Installa fail2ban",
            )
        )
    if ac.tentativi_24h > 100:
        add(
            Controllo(
                ATTENZIONE,
                "Tentativi di accesso",
                f"{ac.tentativi_24h} accessi SSH falliti nelle ultime 24 ore",
                "attiva fail2ban e le chiavi SSH",
            )
        )
    # antivirus
    if not av.installato:
        add(Controllo(ATTENZIONE, "Antivirus", "ClamAV non installato", "Sicurezza → Antivirus → Installa ClamAV"))
    else:
        if av.firme_giorni is not None and av.firme_giorni > 3:
            add(
                Controllo(
                    ATTENZIONE,
                    "Antivirus: firme",
                    f"vecchie di {av.firme_giorni} giorni",
                    "Sicurezza → Antivirus → Aggiorna firme",
                )
            )
        else:
            add(Controllo(OK, "Antivirus", f"ClamAV {av.versione}, firme del {av.firme_del or '?'}"))
        infetti = av.ultima_scansione.get("Infected files", "")
        if infetti and infetti != "0":
            add(
                Controllo(
                    ERRORE,
                    "Antivirus: scansione",
                    f"{infetti} file infetti nell'ultima scansione",
                    f"guarda {LOG_SCANSIONE}",
                )
            )
        elif not av.ultima_scansione:
            add(Controllo(ATTENZIONE, "Antivirus: scansione", "nessuna scansione", "Antivirus → Scansiona"))
    # aggiornamenti
    if ag.disponibile:
        if ag.sicurezza:
            add(
                Controllo(
                    ERRORE,
                    "Aggiornamenti di sicurezza",
                    f"{ag.sicurezza} pacchetti da aggiornare",
                    "Aggiornamenti → Aggiorna il sistema",
                )
            )
        elif ag.pacchetti:
            add(Controllo(ATTENZIONE, "Aggiornamenti", f"{len(ag.pacchetti)} pacchetti da aggiornare"))
        else:
            add(Controllo(OK, "Aggiornamenti", "sistema aggiornato"))
        if not ag.automatici:
            add(
                Controllo(
                    ATTENZIONE,
                    "Aggiornamenti automatici",
                    "unattended-upgrades non attivo",
                    "Aggiornamenti → Attiva gli aggiornamenti automatici",
                )
            )
    if ag.riavvio_richiesto:
        add(
            Controllo(
                ATTENZIONE,
                "Riavvio",
                "il sistema chiede un riavvio dopo gli aggiornamenti",
                "riavvia il server in un momento tranquillo",
            )
        )
    # porte
    ssh_porta = int(ssh.get("port", "22") or 22) if ssh else 22
    attese = PORTE_ATTESE | {ssh_porta}
    estranee = sorted(
        {f"{p.porta}/{p.protocollo} ({p.processo or '?'})" for p in porte if p.pubblica and p.porta not in attese}
    )
    if estranee:
        add(
            Controllo(
                ATTENZIONE,
                "Porte aperte",
                ", ".join(estranee[:8]),
                "chiudi i servizi che non servono o limitali con il firewall",
            )
        )
    elif porte:
        add(Controllo(OK, "Porte aperte", "solo quelle attese (SSH, web)"))
    if env_permessi is not None and env_permessi & 0o077:
        add(Controllo(ERRORE, "File .env", "leggibile da altri utenti", "chmod 600 .env"))
    return c


def peggiore(livelli: list[str]) -> str:
    ordine = {OK: 0, ATTENZIONE: 1, ERRORE: 2}
    return max(livelli, key=lambda x: ordine.get(x, 0), default=OK)


# ============================================================================================== validazioni
# I valori scritti dall'utente finiscono in comandi di sistema (ufw, fail2ban): si accettano solo forme sicure.
def ip_valido(testo: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_network((testo or "").strip(), strict=False)
    except ValueError:
        return False
    return bool(re.fullmatch(r"[0-9a-fA-F:./]+", testo.strip()))


def regola_ufw_valida(testo: str) -> bool:
    """«443», «443/tcp», «8000:8010/udp»."""
    m = re.fullmatch(r"(\d{1,5})(?::(\d{1,5}))?(?:/(tcp|udp))?", (testo or "").strip())
    return bool(m) and all(1 <= int(p) <= 65535 for p in m.groups()[:2] if p)


def numero_regola_ufw(regola: str) -> int | None:
    m = re.match(r"\s*\[\s*(\d+)\]", regola or "")
    return int(m.group(1)) if m else None
