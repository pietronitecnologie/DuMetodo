#!/usr/bin/env python3
"""MetodoConsole: controllo del server Metodo v2 (Docker Compose su Debian).

Stato dei servizi, diagnostica completa, backup / verifica / ripristino del database PostgreSQL e dei file
caricati, aggiornamento, avvisi via email. Solo libreria standard di Python (3.10 o più recente: Debian 12,
Ubuntu 22.04 e successivi).

    metodoconsole stato
    metodoconsole diagnostica [--json]
    metodoconsole backup [--nota TESTO]
    metodoconsole ripristina NOME
    metodoconsole prelievo avvia    (dati da Metodo: verifica | avvia | stato | log | ferma)
    metodoconsole                   (da un terminale: interfaccia a schermo intero, anche via SSH)
    metodoconsole --help            (tutti i comandi)

Configurazione: /etc/metodoconsole/metodoconsole.conf (vedi metodoconsole.conf.example).
"""

from __future__ import annotations

import argparse
import configparser
import contextlib
import datetime as dt
import fcntl
import getpass
import hashlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

VERSIONE = "1.1.0"
CONF_PREDEFINITA = "/etc/metodoconsole/metodoconsole.conf"
OK, ATTENZIONE, ERRORE = "ok", "attenzione", "errore"
GRAVITA = {OK: 0, ATTENZIONE: 1, ERRORE: 2}
SERVIZI = ("db", "redis", "web", "worker", "beat")
FORMATO_NOME = "%Y%m%d-%H%M%S"


class Errore(Exception):
    """Errore da mostrare all'utente così com'è (senza traceback)."""


# ================================================================================================ output
COLORE = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_TINTE = {OK: "32", ATTENZIONE: "33", ERRORE: "31", "titolo": "1", "tenue": "2"}


def tinta(testo: str, tipo: str) -> str:
    return f"\033[{_TINTE[tipo]}m{testo}\033[0m" if COLORE else testo


def info(testo: str) -> None:
    print(testo, flush=True)


def passo(testo: str) -> None:
    print(tinta("» ", "titolo") + testo, flush=True)


def avviso(testo: str) -> None:
    print(tinta("! " + testo, ATTENZIONE), file=sys.stderr, flush=True)


ETICHETTA = {OK: "OK ", ATTENZIONE: "!! ", ERRORE: "ERR"}


@dataclass
class Esito:
    nome: str
    esito: str
    dettaglio: str = ""
    gruppo: str = "server"


def stampa_esiti(esiti: list[Esito]) -> None:
    gruppo = None
    for e in esiti:
        if e.gruppo != gruppo:
            gruppo = e.gruppo
            info(
                tinta(
                    "\n" + {"server": "Server e Docker", "app": "Applicazione Metodo v2"}.get(gruppo, gruppo), "titolo"
                )
            )
        info(f"  {tinta(ETICHETTA[e.esito], e.esito)}  {e.nome:<28} {e.dettaglio}")


def peggiore(esiti: list[Esito]) -> str:
    return max((e.esito for e in esiti), key=GRAVITA.__getitem__, default=OK)


def misura(n: float) -> str:
    for unita in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unita == "TB":
            return f"{n:.0f} {unita}" if unita in ("B", "KB") else f"{n:.1f} {unita}"
        n /= 1024
    return str(n)


def eta(secondi: float) -> str:
    if secondi < 3600:
        return f"{secondi / 60:.0f} min"
    if secondi < 172800:
        return f"{secondi / 3600:.0f} ore"
    return f"{secondi / 86400:.0f} giorni"


# ========================================================================================= configurazione
@dataclass
class Conf:
    cartella: Path = Path("/opt/metodo")
    file_compose: list[str] = field(default_factory=lambda: ["compose.yaml"])
    progetto: str = ""
    url_pubblico: str = ""
    db_nome: str = "metodo"
    db_utente: str = "metodo"
    backup_cartella: Path = Path("/var/backups/metodo")
    conserva_giornalieri: int = 14
    conserva_settimanali: int = 8
    conserva_mensili: int = 12
    backup_massima_eta_ore: int = 26
    copia_remota: str = ""
    spazio_minimo_gb: float = 5.0
    avvisi_email: bool = True
    avvisi_ripeti_ore: int = 6
    cartella_stato: Path = Path("/var/lib/metodoconsole")


def leggi_conf(percorso: str | None) -> Conf:
    c = Conf()
    p = Path(percorso or os.environ.get("METODOCONSOLE_CONF") or CONF_PREDEFINITA)
    if not p.exists():
        if percorso:
            raise Errore(f"File di configurazione non trovato: {p}")
        return c
    ini = configparser.ConfigParser()
    ini.read(p, encoding="utf-8")
    # [dumetodo]: nome della sezione prima della rinomina in Metodo v2 (ancora accettato).
    sezione = "metodo" if ini.has_section("metodo") or not ini.has_section("dumetodo") else "dumetodo"
    d, b, a = (ini[x] if ini.has_section(x) else {} for x in (sezione, "backup", "avvisi"))
    c.cartella = Path(d.get("cartella", str(c.cartella)))
    c.file_compose = d.get("file_compose", "compose.yaml").split()
    c.progetto = d.get("progetto", "")
    c.url_pubblico = d.get("url_pubblico", "").rstrip("/")
    c.db_nome = d.get("db_nome", c.db_nome)
    c.db_utente = d.get("db_utente", c.db_utente)
    c.backup_cartella = Path(b.get("cartella", str(c.backup_cartella)))
    c.conserva_giornalieri = int(b.get("conserva_giornalieri", c.conserva_giornalieri))
    c.conserva_settimanali = int(b.get("conserva_settimanali", c.conserva_settimanali))
    c.conserva_mensili = int(b.get("conserva_mensili", c.conserva_mensili))
    c.backup_massima_eta_ore = int(b.get("massima_eta_ore", c.backup_massima_eta_ore))
    c.copia_remota = b.get("copia_remota", "").strip()
    c.spazio_minimo_gb = float(b.get("spazio_minimo_gb", c.spazio_minimo_gb))
    c.avvisi_email = a.get("email", "si").strip().lower() in ("si", "sì", "yes", "true", "1")
    c.avvisi_ripeti_ore = int(a.get("ripeti_ore", c.avvisi_ripeti_ore))
    c.cartella_stato = Path(a.get("cartella_stato", str(c.cartella_stato)))
    return c


# ================================================================================================ docker
def esegui(
    cmd: list[str],
    *,
    entrata: Any = None,
    uscita: Any = subprocess.PIPE,
    controlla: bool = True,
    timeout: float | None = None,
    testo: bool = True,
    ambiente: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(
            cmd,
            stdin=entrata,
            stdout=uscita,
            # Con uscita=None (comandi lunghi) anche gli avanzamenti di docker vanno direttamente a schermo.
            stderr=None if uscita is None else subprocess.PIPE,
            text=testo,
            timeout=timeout,
            env={**os.environ, **(ambiente or {})},
        )
    except FileNotFoundError:
        raise Errore(f"Comando non trovato: {cmd[0]}") from None
    except subprocess.TimeoutExpired:
        raise Errore(f"Tempo scaduto: {' '.join(cmd[:4])}…") from None
    if controlla and r.returncode != 0:
        err = r.stderr.decode(errors="replace") if isinstance(r.stderr, bytes) else (r.stderr or "")
        raise Errore(f"{' '.join(cmd[:6])} non riuscito (codice {r.returncode}): {(err or '').strip()[-800:]}")
    return r


class Compose:
    def __init__(self, conf: Conf) -> None:
        self.conf = conf

    def base(self) -> list[str]:
        cmd = ["docker", "compose", "--project-directory", str(self.conf.cartella)]
        for f in self.conf.file_compose:
            cmd += ["-f", str(self.conf.cartella / f)]
        if self.conf.progetto:
            cmd += ["-p", self.conf.progetto]
        return cmd

    def __call__(self, *argomenti: str, **kw: Any) -> subprocess.CompletedProcess:
        return esegui(self.base() + list(argomenti), **kw)

    def exec(self, servizio: str, *cmd: str, **kw: Any) -> subprocess.CompletedProcess:
        return self("exec", "-T", servizio, *cmd, **kw)

    def servizi(self) -> dict[str, dict[str, Any]]:
        """Stato dei container: nome servizio -> {State, Health, Status, ...}."""
        r = self("ps", "--all", "--format", "json", controlla=False, timeout=30)
        if r.returncode != 0:
            raise Errore(f"docker compose non risponde: {r.stderr.strip()[-400:]}")
        righe = r.stdout.strip()
        if not righe:
            return {}
        elementi = (
            json.loads(righe) if righe.startswith("[") else [json.loads(x) for x in righe.splitlines() if x.strip()]
        )
        # Il container del prelievo (docker compose run) usa l'immagine di «web»: non va confuso con il servizio.
        return {e["Service"]: e for e in elementi if not str(e.get("Name", "")).endswith("-prelievo")}

    def manage(self, *argomenti: str, **kw: Any) -> subprocess.CompletedProcess:
        return self.exec("web", "python", "manage.py", *argomenti, **kw)

    def attendi_sani(self, servizi: tuple[str, ...] = ("db", "web"), secondi: int = 180) -> bool:
        fine = time.time() + secondi
        while time.time() < fine:
            s = self.servizi()
            if all(
                s.get(x, {}).get("State") == "running" and s.get(x, {}).get("Health", "healthy") in ("healthy", "")
                for x in servizi
            ):
                return True
            time.sleep(3)
        return False


# ============================================================================================== diagnostica
def controlli_server(conf: Conf, dc: Compose) -> list[Esito]:
    esiti: list[Esito] = []
    agg = esiti.append

    r = esegui(["docker", "info", "--format", "{{.ServerVersion}}|{{.DockerRootDir}}"], controlla=False, timeout=20)
    if r.returncode != 0:
        agg(Esito("Docker", ERRORE, "il servizio Docker non risponde (systemctl status docker)"))
        return esiti
    versione_docker, radice_docker = r.stdout.strip().split("|", 1)
    agg(Esito("Docker", OK, f"versione {versione_docker}"))

    if not (conf.cartella / conf.file_compose[0]).exists():
        agg(
            Esito(
                "Installazione",
                ERRORE,
                f"{conf.cartella / conf.file_compose[0]} non trovato (vedi [metodo] cartella)",
            )
        )
        return esiti
    env = conf.cartella / ".env"
    if not env.exists():
        agg(Esito("File .env", ERRORE, f"{env} mancante"))
    elif env.stat().st_mode & 0o077:
        agg(Esito("File .env", ATTENZIONE, f"contiene password ma è leggibile da altri utenti: chmod 600 {env}"))
    else:
        agg(Esito("File .env", OK, "presente, permessi 600"))

    try:
        servizi = dc.servizi()
    except Errore as exc:
        agg(Esito("Servizi", ERRORE, str(exc)))
        return esiti
    for nome in SERVIZI:
        s = servizi.get(nome)
        if s is None:
            agg(Esito(f"Servizio {nome}", ERRORE, "non creato (metodoconsole avvia)"))
            continue
        stato, salute = s.get("State", "?"), s.get("Health", "")
        riavvii = _riavvii(s.get("Name", ""))
        dettaglio = s.get("Status", stato) + (f", {riavvii} riavvii" if riavvii else "")
        if stato != "running" or salute == "unhealthy":
            agg(Esito(f"Servizio {nome}", ERRORE, dettaglio))
        elif salute == "starting" or riavvii > 3:
            agg(Esito(f"Servizio {nome}", ATTENZIONE, dettaglio))
        else:
            agg(Esito(f"Servizio {nome}", OK, dettaglio))
    migrate = servizi.get("migrate")
    if migrate and migrate.get("State") == "exited" and migrate.get("ExitCode", 0) not in (0, None):
        agg(
            Esito(
                "Migrazione all'avvio",
                ERRORE,
                f"terminata con codice {migrate.get('ExitCode')}: metodoconsole log migrate",
            )
        )

    if servizi.get("web", {}).get("State") == "running":
        agg(_salute_web(dc))
        agg(_errori_nei_log(dc))
    if conf.url_pubblico:
        agg(_url_pubblico(conf.url_pubblico))

    for etichetta, percorso in (
        ("Disco sistema", "/"),
        ("Disco Docker", radice_docker),
        ("Disco backup", str(conf.backup_cartella)),
    ):
        p = Path(percorso)
        while not p.exists() and p != p.parent:
            p = p.parent
        u = shutil.disk_usage(p)
        libero = u.free / u.total
        esito = (
            ERRORE
            if libero < 0.05 or u.free < 1 << 30
            else ATTENZIONE
            if libero < 0.15 or u.free < conf.spazio_minimo_gb * (1 << 30)
            else OK
        )
        agg(Esito(etichetta, esito, f"{misura(u.free)} liberi su {misura(u.total)} ({libero:.0%}) — {p}"))

    agg(_ultimo_backup(conf))
    agg(_orologio())
    prelievo = descrivi_prelievo(stato_container(nome_prelievo(conf)))
    if prelievo is not None:
        agg(prelievo)
    return esiti


def _riavvii(container: str) -> int:
    if not container:
        return 0
    r = esegui(["docker", "inspect", "--format", "{{.RestartCount}}", container], controlla=False, timeout=15)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


def _salute_web(dc: Compose) -> Esito:
    script = (
        "import json,urllib.request\n"
        "try:\n r=urllib.request.urlopen('http://127.0.0.1:8000/salute/',timeout=10);print(r.read().decode())\n"
        "except urllib.error.HTTPError as e:print(e.read().decode())\n"
    )
    r = dc.exec("web", "python", "-c", script, controlla=False, timeout=30)
    try:
        dati = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Esito("Risposta web (/salute/)", ERRORE, (r.stderr or r.stdout or "nessuna risposta").strip()[-200:])
    if dati.get("stato") != "ok":
        guasti = ", ".join(k for k, v in dati.get("componenti", {}).items() if not v)
        return Esito("Risposta web (/salute/)", ERRORE, f"non rispondono: {guasti}")
    return Esito("Risposta web (/salute/)", OK, f"versione {dati.get('versione', '?')}")


def _errori_nei_log(dc: Compose) -> Esito:
    r = dc("logs", "--since", "1h", "--no-color", "web", "worker", "beat", controlla=False, timeout=60)
    righe = (r.stdout or "").splitlines()
    errori = [x for x in righe if "Traceback" in x or " ERROR" in x or "CRITICAL" in x]
    if not errori:
        return Esito("Errori nei log (1 ora)", OK, "nessuno")
    return Esito("Errori nei log (1 ora)", ATTENZIONE, f"{len(errori)} righe: metodoconsole log --errori")


def _url_pubblico(url: str) -> Esito:
    import urllib.request

    try:
        r = urllib.request.urlopen(url + "/salute/", timeout=15)  # noqa: S310 - indirizzo dalla configurazione
        dati = json.loads(r.read().decode())
        esito = OK if dati.get("stato") == "ok" else ERRORE
        dettaglio = f"{url} risponde"
    except Exception as exc:
        return Esito("Indirizzo pubblico", ERRORE, f"{url}: {exc}")
    parti = urllib.parse.urlsplit(url)
    if parti.scheme == "https":
        giorni = _giorni_certificato(parti.hostname or "", parti.port or 443)
        if giorni is not None:
            dettaglio += f", certificato valido ancora {giorni} giorni"
            if giorni < 7:
                esito = ERRORE
            elif giorni < 21 and esito == OK:
                esito = ATTENZIONE
    return Esito("Indirizzo pubblico", esito, dettaglio)


def _giorni_certificato(host: str, porta: int) -> int | None:
    try:
        contesto = ssl.create_default_context()
        with (
            socket.create_connection((host, porta), timeout=10) as s,
            contesto.wrap_socket(s, server_hostname=host) as t,
        ):
            scadenza = ssl.cert_time_to_seconds(t.getpeercert()["notAfter"])
        return int((scadenza - time.time()) // 86400)
    except Exception:
        return None


def _ultimo_backup(conf: Conf) -> Esito:
    elenco = elenca_backup(conf)
    if not elenco:
        return Esito("Ultimo backup", ERRORE, f"nessun backup in {conf.backup_cartella}")
    ultimo = elenco[0]
    ore = (time.time() - ultimo.quando.timestamp()) / 3600
    esito = (
        OK if ore <= conf.backup_massima_eta_ore else ATTENZIONE if ore <= conf.backup_massima_eta_ore * 2 else ERRORE
    )
    return Esito("Ultimo backup", esito, f"{ultimo.nome}, {eta(ore * 3600)} fa, {misura(ultimo.dimensione)}")


def _orologio() -> Esito:
    r = (
        esegui(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], controlla=False, timeout=10)
        if shutil.which("timedatectl")
        else None
    )
    if r is None or r.returncode != 0:
        return Esito("Orologio", OK, "sincronizzazione non verificabile")
    return (
        Esito("Orologio", OK, "sincronizzato (NTP)")
        if r.stdout.strip() == "yes"
        else Esito("Orologio", ATTENZIONE, "non sincronizzato: date di consegne e DDT potrebbero essere sbagliate")
    )


def controlli_app(dc: Compose) -> list[Esito]:
    r = dc.manage("diagnostica", "--json", controlla=False, timeout=120)
    try:
        dati = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [Esito("Diagnostica applicazione", ERRORE, (r.stderr or "nessuna risposta").strip()[-300:], "app")]
    return [Esito(c["nome"], c["esito"], c["dettaglio"], "app") for c in dati["controlli"]]


def diagnostica_completa(conf: Conf, dc: Compose) -> list[Esito]:
    esiti = controlli_server(conf, dc)
    if any(e.nome == "Servizio web" and e.esito != ERRORE for e in esiti) or any(
        e.nome == "Risposta web (/salute/)" and e.esito == OK for e in esiti
    ):
        esiti += controlli_app(dc)
    return esiti


# ================================================================================================= backup
@dataclass
class Backup:
    nome: str
    percorso: Path
    quando: dt.datetime
    dimensione: int
    manifesto: dict[str, Any]


def elenca_backup(conf: Conf) -> list[Backup]:
    risultato = []
    if not conf.backup_cartella.is_dir():
        return []
    for p in conf.backup_cartella.iterdir():
        m = p / "manifesto.json"
        if not p.is_dir() or not m.exists():
            continue
        try:
            manifesto = json.loads(m.read_text(encoding="utf-8"))
            quando = dt.datetime.strptime(p.name[:15], FORMATO_NOME)
        except (ValueError, OSError):
            continue
        dimensione = sum(f.stat().st_size for f in p.iterdir() if f.is_file())
        risultato.append(Backup(p.name, p, quando, dimensione, manifesto))
    return sorted(risultato, key=lambda b: b.quando, reverse=True)


def trova_backup(conf: Conf, nome: str) -> Backup:
    elenco = elenca_backup(conf)
    if nome == "ultimo" and elenco:
        return elenco[0]
    for b in elenco:
        if b.nome == nome:
            return b
    # Anche solo l'inizio del nome (la data) o una parte della nota, purché indichi un solo backup.
    trovati = [b for b in elenco if b.nome.startswith(nome)] or [b for b in elenco if nome in b.nome]
    if len(trovati) == 1:
        return trovati[0]
    if trovati:
        raise Errore(f"«{nome}» indica {len(trovati)} backup: " + ", ".join(b.nome for b in trovati[:5]))
    raise Errore(f"Backup «{nome}» non trovato in {conf.backup_cartella} (metodoconsole backup-elenco)")


def impronta(percorso: Path) -> str:
    h = hashlib.sha256()
    with open(percorso, "rb") as f:
        for blocco in iter(lambda: f.read(1 << 20), b""):
            h.update(blocco)
    return h.hexdigest()


@contextlib.contextmanager
def lucchetto(conf: Conf, nome: str):
    """Un solo backup / ripristino / aggiornamento alla volta."""
    conf.cartella_stato.mkdir(parents=True, exist_ok=True)
    with open(conf.cartella_stato / f"{nome}.lock", "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Errore("Un'altra operazione di MetodoConsole è in corso: riprova tra poco.") from None
        yield


def versione_codice(conf: Conf) -> str:
    r = esegui(["git", "-C", str(conf.cartella), "rev-parse", "--short", "HEAD"], controlla=False, timeout=10)
    return r.stdout.strip() if r.returncode == 0 else "sconosciuta"


def esegui_backup(conf: Conf, dc: Compose, nota: str = "", *, con_file: bool = True) -> Backup:
    servizi = dc.servizi()
    if servizi.get("db", {}).get("State") != "running":
        raise Errore("Il database non è in esecuzione: impossibile fare il backup (metodoconsole avvia).")
    adesso = dt.datetime.now()
    suffisso = "".join(c if c.isalnum() or c in "-_" else "-" for c in nota.strip())[:40]
    nome = adesso.strftime(FORMATO_NOME) + (f"-{suffisso}" if suffisso else "")
    conf.backup_cartella.mkdir(parents=True, exist_ok=True)
    os.chmod(conf.backup_cartella, 0o700)
    temporanea = conf.backup_cartella / f".{nome}.incompleto"
    temporanea.mkdir(mode=0o700)
    try:
        passo("Database (pg_dump)…")
        with open(temporanea / "database.dump", "wb") as f:
            dc.exec(
                "db",
                "pg_dump",
                "-U",
                conf.db_utente,
                "-d",
                conf.db_nome,
                "--format=custom",
                "--compress=6",
                uscita=f,
                testo=False,
                timeout=3600,
            )
        with open(temporanea / "database.dump", "rb") as f:
            r = dc.exec("db", "pg_restore", "--list", entrata=f, testo=False, timeout=600)
        tabelle = sum(1 for x in r.stdout.decode(errors="replace").splitlines() if " TABLE DATA " in x)
        file_media = 0
        if con_file and servizi.get("web", {}).get("State") == "running":
            passo("File caricati (foto, allegati, bolle)…")
            with open(temporanea / "file.tar.gz", "wb") as f:
                dc.exec("web", "tar", "-C", "/app/media", "-czf", "-", ".", uscita=f, testo=False, timeout=3600)
            r = esegui(["tar", "-tzf", str(temporanea / "file.tar.gz")])
            file_media = sum(1 for x in r.stdout.splitlines() if not x.endswith("/"))
        elif con_file:
            avviso("Il servizio web è fermo: backup dei soli dati del database (senza i file caricati).")
        env = conf.cartella / ".env"
        if env.exists():
            shutil.copy2(env, temporanea / "env")
            os.chmod(temporanea / "env", 0o600)
        r = dc.exec(
            "db", "psql", "-U", conf.db_utente, "-d", conf.db_nome, "-tAc", "SHOW server_version", controlla=False
        )
        manifesto = {
            "creato_il": adesso.isoformat(timespec="seconds"),
            "creato_da": f"MetodoConsole {VERSIONE}",
            "host": socket.gethostname(),
            "versione_metodo": versione_codice(conf),
            "postgresql": r.stdout.strip(),
            "database": conf.db_nome,
            "tabelle_con_dati": tabelle,
            "file_caricati": file_media,
            "nota": nota,
            "file": {
                f.name: {"sha256": impronta(f), "byte": f.stat().st_size}
                for f in sorted(temporanea.iterdir())
                if f.is_file()
            },
        }
        (temporanea / "manifesto.json").write_text(
            json.dumps(manifesto, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        definitiva = conf.backup_cartella / nome
        temporanea.rename(definitiva)
    except BaseException:
        shutil.rmtree(temporanea, ignore_errors=True)
        raise
    b = trova_backup(conf, nome)
    info(tinta(f"Backup {nome} creato: {misura(b.dimensione)}, {tabelle} tabelle, {file_media} file.", OK))
    return b


def verifica_backup(conf: Conf, dc: Compose | None, b: Backup) -> list[str]:
    """Problemi trovati (lista vuota = backup integro)."""
    problemi = []
    for nome, atteso in b.manifesto.get("file", {}).items():
        f = b.percorso / nome
        if not f.exists():
            problemi.append(f"{nome}: mancante")
        elif impronta(f) != atteso["sha256"]:
            problemi.append(f"{nome}: contenuto alterato (impronta diversa)")
    if "database.dump" not in b.manifesto.get("file", {}):
        problemi.append("database.dump assente dal manifesto")
    elif dc is not None and not problemi:
        with open(b.percorso / "database.dump", "rb") as f:
            r = dc.exec("db", "pg_restore", "--list", entrata=f, testo=False, controlla=False, timeout=600)
        if r.returncode != 0:
            problemi.append("database.dump non leggibile da pg_restore")
    return problemi


def da_eliminare(elenco: list[Backup], giornalieri: int, settimanali: int, mensili: int) -> list[Backup]:
    """Rotazione: l'ultimo backup di ognuno degli ultimi N giorni, N settimane e N mesi; il resto si elimina.

    I backup con una nota (es. «prima-del-ripristino») sono tenuti come giornalieri ma non contano oltre.
    """
    tenere: set[str] = set()
    for chiave, quanti in (
        (lambda d: d.date(), giornalieri),
        (lambda d: d.isocalendar()[:2], settimanali),
        (lambda d: (d.year, d.month), mensili),
    ):
        visti: list[Any] = []
        for b in elenco:  # dal più recente
            k = chiave(b.quando)
            if k not in visti:
                if len(visti) >= quanti:
                    break
                visti.append(k)
                tenere.add(b.nome)
    if elenco:
        tenere.add(elenco[0].nome)  # mai eliminare l'ultimo
    return [b for b in elenco if b.nome not in tenere]


def ruota_backup(conf: Conf) -> None:
    for b in da_eliminare(
        elenca_backup(conf), conf.conserva_giornalieri, conf.conserva_settimanali, conf.conserva_mensili
    ):
        shutil.rmtree(b.percorso)
        info(tinta(f"  eliminato il backup vecchio {b.nome}", "tenue"))


def copia_remota(conf: Conf) -> None:
    if not conf.copia_remota:
        return
    passo(f"Copia dei backup su {conf.copia_remota}…")
    esegui(
        ["rsync", "-a", "--delete", "--exclude", ".*", f"{conf.backup_cartella}/", conf.copia_remota], timeout=6 * 3600
    )


# ================================================================================================= avvisi
def invia_avviso(conf: Conf, dc: Compose, oggetto: str, testo: str) -> bool:
    if not conf.avvisi_email:
        return False
    try:
        p = subprocess.run(
            dc.base() + ["exec", "-T", "web", "python", "manage.py", "avviso_server", "--oggetto", oggetto],
            input=testo,
            text=True,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        p = None
    if p is None or p.returncode != 0:
        avviso("Avviso email non inviato" + (f": {p.stderr.strip()[-300:]}" if p else ""))
        return False
    return True


def stato_avvisi(conf: Conf) -> dict[str, Any]:
    try:
        return json.loads((conf.cartella_stato / "avvisi.json").read_text())
    except (OSError, ValueError):
        return {}


def salva_stato_avvisi(conf: Conf, dati: dict[str, Any]) -> None:
    conf.cartella_stato.mkdir(parents=True, exist_ok=True)
    (conf.cartella_stato / "avvisi.json").write_text(json.dumps(dati))


def decidi_avviso(precedente: dict[str, Any], esito: str, firma: str, adesso: float, ripeti_ore: int) -> str | None:
    """'problema', 'risolto' o None.

    Avvisa quando le cose peggiorano o cambiano, poi ogni `ripeti_ore` finché durano, e quando si risolvono.
    """
    era = precedente.get("esito", OK)
    if esito == OK:
        return "risolto" if era != OK else None
    if esito != era or firma != precedente.get("firma"):
        return "problema"
    if adesso - precedente.get("avvisato_il", 0) >= ripeti_ore * 3600:
        return "problema"
    return None


# ================================================================================================ comandi
def cmd_stato(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    servizi = dc.servizi()
    info(tinta(f"Metodo v2 su {socket.gethostname()} — {conf.cartella} (codice {versione_codice(conf)})", "titolo"))
    if not servizi:
        info("  Nessun container: metodoconsole avvia")
        return 2
    for nome in SERVIZI + ("migrate",):
        s = servizi.get(nome)
        if not s:
            continue
        salute = s.get("Health") or ""
        tipo = (
            OK
            if s.get("State") == "running" and salute in ("", "healthy")
            else (OK if nome == "migrate" and s.get("ExitCode") == 0 else ERRORE)
        )
        info(f"  {tinta(ETICHETTA[tipo], tipo)}  {nome:<8} {s.get('Status', s.get('State'))}")
    elenco = elenca_backup(conf)
    info(f"\n  Backup: {len(elenco)} in {conf.backup_cartella}" + (f", ultimo {elenco[0].nome}" if elenco else ""))
    return 0


def cmd_diagnostica(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    esiti = diagnostica_completa(conf, dc)
    esito = peggiore(esiti)
    if a.json:
        print(json.dumps({"esito": esito, "controlli": [asdict(e) for e in esiti]}, ensure_ascii=False, indent=2))
    elif not getattr(a, "silenzioso", False) or esito != OK:
        stampa_esiti(esiti)
        info(f"\nEsito: {tinta(esito.upper(), esito)}")
    if getattr(a, "avvisa", False):
        problemi = [e for e in esiti if e.esito != OK]
        firma = "|".join(sorted(f"{e.nome}:{e.esito}" for e in problemi))
        prec = stato_avvisi(conf)
        decisione = decidi_avviso(prec, esito, firma, time.time(), conf.avvisi_ripeti_ore)
        nuovo = {"esito": esito, "firma": firma, "avvisato_il": prec.get("avvisato_il", 0)}
        if decisione:
            if decisione == "risolto":
                oggetto, testo = "Problemi risolti", "Tutti i controlli di MetodoConsole sono tornati OK.\n"
            else:
                oggetto = f"{esito.upper()}: " + ", ".join(e.nome for e in problemi if e.esito == esito)[:120]
                testo = (
                    "MetodoConsole ha trovato questi problemi:\n\n"
                    + "\n".join(f"[{e.esito.upper()}] {e.nome}: {e.dettaglio}" for e in problemi)
                    + f"\n\nServer: {socket.gethostname()}\nPer i dettagli: metodoconsole diagnostica\n"
                )
            if invia_avviso(conf, dc, oggetto, testo):
                nuovo["avvisato_il"] = time.time()
        salva_stato_avvisi(conf, nuovo)
    return GRAVITA[esito]


def cmd_backup(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    with lucchetto(conf, "operazione"):
        try:
            b = esegui_backup(conf, dc, a.nota or "", con_file=not a.solo_database)
            problemi = verifica_backup(conf, dc, b)
            if problemi:
                raise Errore("Backup creato ma non integro: " + "; ".join(problemi))
            if not a.nota:
                ruota_backup(conf)
            copia_remota(conf)
        except Errore as exc:
            if a.avvisa:
                invia_avviso(conf, dc, "Backup NON riuscito", f"Il backup di Metodo v2 non è riuscito:\n\n{exc}\n")
            raise
    return 0


def versione_backup(m: dict[str, Any]) -> str:
    """Versione di Metodo v2 con cui è stato fatto il backup (i backup di DuMetodo usavano un'altra chiave)."""
    return str(m.get("versione_metodo") or m.get("versione_dumetodo") or "?")


def cmd_backup_elenco(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    elenco = elenca_backup(conf)
    if not elenco:
        info(f"Nessun backup in {conf.backup_cartella}")
        return 0
    info(f"{'NOME':<34} {'DIMENSIONE':>10}  {'VERSIONE':<9} NOTA")
    for b in elenco:
        m = b.manifesto
        info(f"{b.nome:<34} {misura(b.dimensione):>10}  {versione_backup(m):<9} {m.get('nota', '')}")
    return 0


def cmd_backup_verifica(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    b = trova_backup(conf, a.nome)
    problemi = verifica_backup(conf, dc, b)
    if problemi:
        for p in problemi:
            info(tinta(f"  ERR  {p}", ERRORE))
        return 2
    m = b.manifesto
    info(
        tinta(f"Backup {b.nome} integro", OK)
        + f": {m.get('tabelle_con_dati')} tabelle, {m.get('file_caricati')} file, "
        f"versione {versione_backup(m)}, PostgreSQL {m.get('postgresql')}"
    )
    return 0


def conferma(domanda: str, parola: str, forzato: bool) -> None:
    if forzato:
        return
    if not sys.stdin.isatty():
        raise Errore("Conferma richiesta: rilancia da terminale oppure aggiungi --si.")
    risposta = input(f"{domanda}\nScrivi {parola} per confermare: ").strip()
    if risposta != parola:
        raise Errore("Operazione annullata.")


def cmd_ripristina(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    b = trova_backup(conf, a.nome)
    problemi = verifica_backup(conf, dc, b)
    if problemi:
        raise Errore("Il backup non è integro, ripristino annullato: " + "; ".join(problemi))
    m = b.manifesto
    info(tinta(f"Ripristino del backup {b.nome}", "titolo"))
    info(f"  creato il {m.get('creato_il')} su {m.get('host')}, versione Metodo v2 {versione_backup(m)}")
    info(f"  {m.get('tabelle_con_dati')} tabelle, {m.get('file_caricati')} file caricati")
    con_file = "file.tar.gz" in m.get("file", {}) and not a.solo_database
    conferma(
        tinta(
            "TUTTI i dati attuali di Metodo v2 saranno sostituiti"
            + (" (anche i file caricati)" if con_file else "")
            + " con quelli del backup.",
            ATTENZIONE,
        ),
        "RIPRISTINA",
        a.si,
    )
    with lucchetto(conf, "operazione"):
        if not a.senza_backup_sicurezza:
            passo("Backup di sicurezza dei dati attuali…")
            esegui_backup(conf, dc, "prima-del-ripristino")
        passo("Fermo web, worker e beat…")
        dc("stop", "web", "worker", "beat", timeout=180)
        passo("Ricreo il database…")
        psql = ["psql", "-v", "ON_ERROR_STOP=1", "-U", conf.db_utente, "-d", "postgres", "-c"]
        dc.exec("db", *psql, f'DROP DATABASE IF EXISTS "{conf.db_nome}" WITH (FORCE)', timeout=300)
        dc.exec("db", *psql, f'CREATE DATABASE "{conf.db_nome}" OWNER "{conf.db_utente}"', timeout=300)
        passo("Carico i dati (pg_restore)…")
        with open(b.percorso / "database.dump", "rb") as f:
            dc.exec(
                "db",
                "pg_restore",
                "-U",
                conf.db_utente,
                "-d",
                conf.db_nome,
                "--no-owner",
                f"--role={conf.db_utente}",
                "--exit-on-error",
                entrata=f,
                testo=False,
                timeout=6 * 3600,
            )
        if con_file:
            passo("Ripristino i file caricati…")
            with open(b.percorso / "file.tar.gz", "rb") as f:
                dc(
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "--entrypoint",
                    "sh",
                    "web",
                    "-c",
                    "find /app/media -mindepth 1 -delete && tar -xzf - -C /app/media",
                    entrata=f,
                    testo=False,
                    timeout=6 * 3600,
                )
        passo("Riavvio Metodo v2 (con le migrazioni del codice attuale)…")
        dc("up", "-d", timeout=900)
        if not dc.attendi_sani():
            avviso("Metodo v2 non è tornato in salute entro 3 minuti: metodoconsole diagnostica")
            return 2
    env_backup = b.percorso / "env"
    if (
        env_backup.exists()
        and (conf.cartella / ".env").exists()
        and impronta(env_backup) != impronta(conf.cartella / ".env")
    ):
        avviso(
            f"Il file .env del backup è diverso da quello attuale. Se il backup viene da un altro server, "
            f"la password della casella ordini (cifrata con DJANGO_SECRET_KEY) va reinserita in Configurazione, "
            f"oppure copia {env_backup} in {conf.cartella / '.env'}."
        )
    info(tinta(f"Ripristino del backup {b.nome} completato.", OK))
    return 0


def cmd_aggiorna(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    with lucchetto(conf, "operazione"):
        prima = versione_codice(conf)
        sporco = esegui(
            ["git", "-C", str(conf.cartella), "status", "--porcelain", "--untracked-files=no"]
        ).stdout.strip()
        if sporco:
            raise Errore(f"Ci sono file modificati in {conf.cartella}: aggiornamento annullato.\n{sporco}")
        git = ["git", "-C", str(conf.cartella)]
        passo("Controllo se c'è codice nuovo (git fetch)…")
        esegui([*git, "fetch", "--quiet"], timeout=300)
        dopo = esegui([*git, "rev-parse", "--short", "@{u}"]).stdout.strip()
        if dopo == prima and not a.forza:
            info(f"Già aggiornato (versione {dopo}).")
            return 0
        backup = None
        if not a.senza_backup:
            # Prima di toccare il codice: il backup porta la versione con cui i dati sono stati scritti.
            passo("Backup prima dell'aggiornamento…")
            backup = esegui_backup(conf, dc, f"prima-di-{dopo}")
        passo(f"Aggiorno il codice ({prima} → {dopo})…")
        esegui([*git, "merge", "--ff-only", "--quiet", "@{u}"], timeout=300)
        passo("Ricostruisco e riavvio…")
        dc("up", "-d", "--build", "--remove-orphans", ambiente={"METODO_VERSIONE": dopo}, uscita=None, timeout=3600)
        if not dc.attendi_sani():
            indietro = f"git -C {conf.cartella} reset --hard {prima} && metodoconsole avvia --ricostruisci"
            if backup:
                indietro += f" && metodoconsole ripristina {backup.nome}"
            avviso(f"Metodo v2 non è tornato in salute (metodoconsole diagnostica). Per tornare indietro: {indietro}")
            return 2
    info(tinta(f"Aggiornato alla versione {dopo}.", OK))
    return cmd_diagnostica(conf, dc, argparse.Namespace(json=False, silenzioso=False, avvisa=False))


def cmd_avvia(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    extra = ["--build"] if a.ricostruisci else []
    dc("up", "-d", *extra, *a.servizi, ambiente={"METODO_VERSIONE": versione_codice(conf)}, uscita=None, timeout=3600)
    return 0 if dc.attendi_sani() else 2


def cmd_ferma(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    dc("stop", *a.servizi, uscita=None, timeout=300)
    return 0


def cmd_riavvia(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    dc("restart", *a.servizi, uscita=None, timeout=300)
    return 0 if dc.attendi_sani() else 2


def cmd_log(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    argomenti = ["logs", "--no-color", "--tail", str(a.righe)]
    if a.segui:
        argomenti.append("--follow")
    if a.errori:
        r = dc(*argomenti, "--since", a.da or "24h", *a.servizi, controlla=False)
        for riga in r.stdout.splitlines():
            if any(x in riga for x in ("Traceback", " ERROR", "CRITICAL", "Error:", "Exception")):
                info(riga)
        return 0
    if a.da:
        argomenti += ["--since", a.da]
    with contextlib.suppress(KeyboardInterrupt):
        subprocess.run(dc.base() + argomenti + a.servizi)
    return 0


def cmd_manage(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    return subprocess.run(dc.base() + ["exec", "web", "python", "manage.py", *a.argomenti]).returncode


def cmd_psql(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    return subprocess.run(dc.base() + ["exec", "db", "psql", "-U", conf.db_utente, "-d", conf.db_nome]).returncode


VENV_INTERFACCIA = Path("/opt/metodoconsole/venv")


def cmd_pannello(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    """Interfaccia a schermo intero (Textual, nel pacchetto tui/ accanto a questo file).

    Textual non fa parte della libreria standard: installa.sh lo mette in un ambiente dedicato
    (/opt/metodoconsole/venv). Se il Python attuale non lo trova, si rilancia con quello."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise Errore("L'interfaccia va aperta da un terminale (anche via SSH: ssh -t server metodoconsole).")
    cartella = str(Path(__file__).resolve().parent)
    if cartella not in sys.path:
        sys.path.insert(0, cartella)
    try:
        import textual  # noqa: F401
    except ImportError:
        python = VENV_INTERFACCIA / "bin" / "python"
        if python.exists() and Path(sys.executable).resolve() != python.resolve():
            os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])  # noqa: S606
        raise Errore(
            "Manca Textual per l'interfaccia: sudo ./MetodoConsole/installa.sh (oppure pip install textual). "
            "I comandi da riga di comando funzionano anche senza."
        ) from None
    from tui import app

    return app.avvia(conf, dc, sys.modules[__name__])


def cmd_versione(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    info(f"MetodoConsole {VERSIONE} — Metodo v2 {versione_codice(conf)} in {conf.cartella}")
    return 0


# ============================================================================================ prelievo
LOG_PRELIEVO = "/app/media/prelievo-metodo.log"  # nel volume dei file: resta anche dopo il container


def nome_prelievo(conf: Conf) -> str:
    """Container dedicato al prelievo: separato da «web», continua anche se si chiude la sessione SSH."""
    base = conf.progetto or conf.cartella.name or "metodo"
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in base.lower()) + "-prelievo"


def leggi_env(conf: Conf) -> dict[str, str]:
    valori: dict[str, str] = {}
    env = conf.cartella / ".env"
    if not env.exists():
        return valori
    for riga in env.read_text(encoding="utf-8", errors="replace").splitlines():
        riga = riga.strip()
        if riga and not riga.startswith("#") and "=" in riga:
            k, v = riga.split("=", 1)
            valori[k.strip()] = v.strip().strip("'\"")
    return valori


def url_metodo(conf: Conf, *, chiedi: bool = True) -> dict[str, str]:
    """Ambiente da passare al container: vuoto se METODO_DB_URL è già nel .env di Metodo v2.

    In alternativa si legge dall'ambiente o si chiede (senza eco): l'URL contiene una password e così non
    finisce né nella riga di comando (visibile con ps) né nella cronologia della shell."""
    if leggi_env(conf).get("METODO_DB_URL"):
        return {}
    if os.environ.get("METODO_DB_URL"):
        return {"METODO_DB_URL": os.environ["METODO_DB_URL"]}
    if not chiedi or not sys.stdin.isatty():
        raise Errore(
            f"Manca l'indirizzo del database di Metodo: aggiungi METODO_DB_URL a {conf.cartella / '.env'} "
            "(vedi .env.example) oppure esporta la variabile prima del comando."
        )
    url = getpass.getpass("Database di Metodo (postgres://utente:password@host:porta/nome): ").strip()
    if not url.startswith(("postgres://", "postgresql://")):
        raise Errore("Indirizzo non valido: deve iniziare con postgres://")
    return {"METODO_DB_URL": url}


def stato_container(nome: str) -> dict[str, Any] | None:
    r = esegui(["docker", "inspect", "--format", "{{json .State}}", nome], controlla=False, timeout=20)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return json.loads(r.stdout)


def _ora_docker(testo: str) -> str:
    try:
        # timezone.utc e non datetime.UTC: MetodoConsole gira anche con Python 3.10 (Ubuntu 22.04)
        quando = dt.datetime.fromisoformat(testo[:19]).replace(tzinfo=dt.timezone.utc).astimezone()  # noqa: UP017
    except ValueError:
        return testo
    return quando.strftime("%d/%m/%Y %H:%M")


def descrivi_prelievo(st: dict[str, Any] | None) -> Esito | None:
    if st is None:
        return None
    if st.get("Running"):
        return Esito("Prelievo da Metodo", OK, f"in corso dal {_ora_docker(st.get('StartedAt', ''))}", "app")
    codice = st.get("ExitCode")
    fine = _ora_docker(st.get("FinishedAt", ""))
    if codice == 0:
        return Esito("Prelievo da Metodo", OK, f"completato il {fine}", "app")
    return Esito(
        "Prelievo da Metodo",
        ATTENZIONE,
        f"interrotto il {fine} (codice {codice}): metodoconsole prelievo log, poi prelievo avvia per riprendere",
        "app",
    )


def opzioni_prelievo(a: argparse.Namespace) -> list[str]:
    opzioni = ["--log", LOG_PRELIEVO]
    for nome in ("blocco", "max_kb", "pausa", "timeout", "tentativi"):
        valore = getattr(a, nome, None)
        if valore is not None:
            opzioni += [f"--{nome.replace('_', '-')}", str(valore)]
    if getattr(a, "solo", None):
        opzioni += ["--solo", *a.solo]
    if getattr(a, "ricomincia", None):
        opzioni += ["--ricomincia", *a.ricomincia]
    return opzioni


def cmd_prelievo(conf: Conf, dc: Compose, a: argparse.Namespace) -> int:
    nome = nome_prelievo(conf)
    st = stato_container(nome)
    in_corso = bool(st and st.get("Running"))

    if a.azione == "verifica":
        amb = url_metodo(conf)
        cmd = ["run", "--rm", "-T", *(["-e", "METODO_DB_URL"] if amb else []), "web"]
        return dc(
            *cmd,
            "python",
            "manage.py",
            "preleva_da_metodo",
            "--verifica",
            ambiente=amb,
            uscita=None,
            controlla=False,
            timeout=600,
        ).returncode

    if a.azione == "avvia":
        if in_corso:
            raise Errore("Un prelievo è già in corso: metodoconsole prelievo stato / prelievo log -f")
        amb = url_metodo(conf)
        if not a.senza_backup:
            with lucchetto(conf, "operazione"):
                passo("Backup di sicurezza prima del prelievo…")
                b = esegui_backup(conf, dc, "prima-del-prelievo", con_file=False)
                info(f"  backup {b.nome}")
        if st is not None:
            esegui(["docker", "rm", nome], timeout=60)  # il container del prelievo precedente, già fermo
        passo("Avvio il prelievo in un container dedicato…")
        dc(
            "run",
            "-d",
            "--name",
            nome,
            *(["-e", "METODO_DB_URL"] if amb else []),
            "web",
            "python",
            "manage.py",
            "preleva_da_metodo",
            *opzioni_prelievo(a),
            ambiente=amb,
            timeout=600,
        )
        info(tinta("Prelievo avviato: continua anche se chiudi la sessione.", OK))
        info("  Avanzamento:  metodoconsole prelievo log -f")
        info("  Stato:        metodoconsole prelievo stato")
        info("  Interrompi:   metodoconsole prelievo ferma   (poi «prelievo avvia» riprende da dove era)")
        return 0

    if a.azione == "stato":
        e = descrivi_prelievo(st)
        if e is None:
            info("Nessun prelievo avviato con MetodoConsole.")
        else:
            info(f"  {tinta(ETICHETTA[e.esito], e.esito)}  {e.dettaglio}")
        if dc.servizi().get("web", {}).get("State") == "running":
            info(tinta("\nAvanzamento salvato in Metodo v2", "titolo"))
            r = dc.manage("preleva_da_metodo", "--stato", controlla=False, timeout=60)
            info(r.stdout.rstrip() or r.stderr.strip())
        if st is not None:
            info(tinta("\nUltime righe del log", "titolo"))
            r = esegui(["docker", "logs", "--tail", "8", nome], controlla=False, timeout=30)
            info((r.stdout + r.stderr).rstrip())
        return 0

    if st is None:
        info("Nessun prelievo avviato con MetodoConsole.")
        return 0

    if a.azione == "log":
        argomenti = ["docker", "logs", "--tail", str(a.righe)] + (["--follow"] if a.segui else []) + [nome]
        with contextlib.suppress(KeyboardInterrupt):
            subprocess.run(argomenti)
        return 0

    # ferma: Ctrl+C al comando, che chiude il blocco in corso e stampa il riepilogo
    if not in_corso:
        info("Il prelievo non è in corso.")
        return 0
    passo("Interrompo il prelievo…")
    esegui(["docker", "kill", "--signal", "SIGINT", nome], controlla=False, timeout=30)
    fine = time.time() + 120
    while time.time() < fine and (stato_container(nome) or {}).get("Running"):
        time.sleep(2)
    if (stato_container(nome) or {}).get("Running"):
        esegui(["docker", "stop", nome], controlla=False, timeout=120)
    info("Prelievo interrotto. Rilancia «metodoconsole prelievo avvia» per riprendere da dove era arrivato.")
    return 0


# ================================================================================================== main
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="metodoconsole", description="Controllo del server Metodo v2 (Docker su Debian).")
    p.add_argument("--conf", help=f"file di configurazione (predefinito {CONF_PREDEFINITA})")
    s = p.add_subparsers(dest="comando", required=True, metavar="COMANDO")

    def sub(nome: str, funzione: Any, aiuto: str) -> argparse.ArgumentParser:
        x = s.add_parser(nome, help=aiuto, description=aiuto)
        x.set_defaults(funzione=funzione)
        return x

    sub("stato", cmd_stato, "servizi in esecuzione e ultimo backup")
    x = sub("diagnostica", cmd_diagnostica, "controllo completo di server, Docker e applicazione (uscita 0/1/2)")
    x.add_argument("--json", action="store_true")
    x.add_argument("--avvisa", action="store_true", help="invia un'email se ci sono problemi (o quando si risolvono)")
    x.add_argument("--silenzioso", action="store_true", help="stampa solo se qualcosa non va")
    x = sub("backup", cmd_backup, "backup di database, file caricati e .env, con verifica e rotazione")
    x.add_argument("--nota", help="breve descrizione (i backup con nota non vengono ruotati automaticamente)")
    x.add_argument("--solo-database", action="store_true", help="senza i file caricati")
    x.add_argument("--avvisa", action="store_true", help="email se il backup non riesce")
    sub("backup-elenco", cmd_backup_elenco, "elenco dei backup")
    x = sub("backup-verifica", cmd_backup_verifica, "controlla integrità e leggibilità di un backup")
    x.add_argument("nome", help="nome del backup (anche solo l'inizio) o «ultimo»")
    x = sub("ripristina", cmd_ripristina, "ripristina database e file da un backup (fa prima un backup di sicurezza)")
    x.add_argument("nome", help="nome del backup (anche solo l'inizio) o «ultimo»")
    x.add_argument("--solo-database", action="store_true", help="lascia i file caricati come sono")
    x.add_argument("--senza-backup-sicurezza", action="store_true")
    x.add_argument("--si", action="store_true", help="non chiedere conferma")
    x = sub("aggiorna", cmd_aggiorna, "git pull, backup, ricostruzione e riavvio, diagnostica")
    x.add_argument("--senza-backup", action="store_true")
    x.add_argument("--forza", action="store_true", help="ricostruisci anche se il codice non è cambiato")
    for nome, funzione, aiuto in (
        ("avvia", cmd_avvia, "avvia Metodo v2 (o i servizi indicati)"),
        ("ferma", cmd_ferma, "ferma Metodo v2 (o i servizi indicati)"),
        ("riavvia", cmd_riavvia, "riavvia Metodo v2 (o i servizi indicati)"),
    ):
        x = sub(nome, funzione, aiuto)
        x.add_argument("servizi", nargs="*", metavar="SERVIZIO")
        if nome == "avvia":
            x.add_argument("--ricostruisci", action="store_true", help="ricostruisci l'immagine dal codice attuale")
    x = sub("log", cmd_log, "log dei servizi")
    x.add_argument("servizi", nargs="*", metavar="SERVIZIO")
    x.add_argument("-f", "--segui", action="store_true")
    x.add_argument("-n", "--righe", type=int, default=200)
    x.add_argument("--da", help="es. 2h, 30m, 2026-01-31")
    x.add_argument("--errori", action="store_true", help="solo righe di errore (ultime 24 ore)")
    x = sub("manage", cmd_manage, "esegue un comando di Django (es. manage createsuperuser)")
    x.add_argument("argomenti", nargs=argparse.REMAINDER)
    sub("psql", cmd_psql, "console SQL del database")
    x = sub("prelievo", cmd_prelievo, "preleva i dati dal Metodo in produzione (a blocchi, riprendibile)")
    x.add_argument(
        "azione",
        choices=("verifica", "avvia", "stato", "log", "ferma"),
        help="verifica: connessione e volumi · avvia: avvia o riprende · stato · log · ferma",
    )
    x.add_argument("--max-kb", type=float, help="banda massima in KB/s (predefinito 1024, 0 = nessun limite)")
    x.add_argument("--pausa", type=float, help="secondi di pausa tra due letture (predefinito 0.2)")
    x.add_argument("--blocco", type=int, help="record per lettura (predefinito 2000)")
    x.add_argument("--timeout", type=int, help="durata massima di una lettura su Metodo, in secondi")
    x.add_argument("--tentativi", type=int, help="tentativi se Metodo non risponde")
    x.add_argument("--solo", nargs="+", metavar="PASSO", help="solo questi passi (es. ordini righe)")
    x.add_argument("--ricomincia", nargs="+", metavar="PASSO", help="rilegge e aggiorna (ordini righe consegne…)")
    x.add_argument("--senza-backup", action="store_true", help="avvia senza il backup di sicurezza")
    x.add_argument("-f", "--segui", action="store_true", help="con «log»: segue l'avanzamento")
    x.add_argument("-n", "--righe", type=int, default=50, help="con «log»: righe da mostrare")
    sub("pannello", cmd_pannello, "interfaccia a schermo intero: Docker, sicurezza, disco, backup (anche via SSH)")
    sub("versione", cmd_versione, "versione di MetodoConsole e di Metodo v2")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv and sys.stdin.isatty() and sys.stdout.isatty():
        argv = ["pannello"]  # «metodoconsole» da solo, in un terminale, apre il pannello
    a = parser().parse_args(argv)
    try:
        conf = leggi_conf(a.conf)
        return a.funzione(conf, Compose(conf), a)
    except Errore as exc:
        print(tinta(f"Errore: {exc}", ERRORE), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrotto.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
