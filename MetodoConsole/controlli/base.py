"""Pezzi comuni ai controlli: esito, esecuzione di comandi, porte, certificati, date di fine supporto."""

from __future__ import annotations

import configparser
import contextlib
import datetime as dt
import os
import re
import shutil
import socket
import ssl
import subprocess
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as Scaduto
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OK, ATTENZIONE, ERRORE = "ok", "attenzione", "errore"
GRAVITA = {OK: 0, ATTENZIONE: 1, ERRORE: 2}
NOMI_GRUPPI = {
    "server": "Metodo v2: server e Docker",
    "app": "Metodo v2: applicazione",
    "sistema": "Sistema",
    "sicurezza": "Sicurezza",
    "web": "Web (nginx / Apache) e certificati",
    "posta": "Posta",
    "database": "Database",
    "django": "Applicazioni Django",
    "docker": "Docker",
}
SERVE_ROOT = "non verificato: serve sudo"


@dataclass
class Esito:
    nome: str
    esito: str
    dettaglio: str = ""
    gruppo: str = "server"


def peggiore(livelli: Iterable[str]) -> str:
    return max(livelli, key=GRAVITA.__getitem__, default=OK)


@dataclass
class Contesto:
    """Configurazione delle aree (sezioni di metodoconsole.conf) e informazioni sul server lette una volta sola."""

    ini: configparser.ConfigParser = field(default_factory=configparser.ConfigParser)
    oggi: dt.date = field(default_factory=dt.date.today)
    _cache: dict[str, Any] = field(default_factory=dict)

    @property
    def root(self) -> bool:
        return hasattr(os, "geteuid") and os.geteuid() == 0

    def valore(self, sezione: str, chiave: str, predefinito: str = "") -> str:
        if self.ini.has_section(sezione):
            return self.ini[sezione].get(chiave, predefinito).strip()
        return predefinito

    def lista(self, sezione: str, chiave: str, predefinito: str = "") -> list[str]:
        return [x for x in re.split(r"[\s,]+", self.valore(sezione, chiave, predefinito)) if x]

    def sezioni(self, prefisso: str) -> dict[str, dict[str, str]]:
        """Le sezioni [prefisso:nome] → {nome: {chiave: valore}}."""
        return {
            s.split(":", 1)[1].strip(): {k: v.strip() for k, v in self.ini[s].items()}
            for s in self.ini.sections()
            if s.startswith(prefisso + ":")
        }

    def memo(self, chiave: str, calcola: Any) -> Any:
        if chiave not in self._cache:
            self._cache[chiave] = calcola()
        return self._cache[chiave]

    def porte(self) -> list[Porta]:
        return self.memo("porte", porte_in_ascolto)


# --------------------------------------------------------------------------------------------- comandi
@dataclass
class Uscita:
    codice: int
    testo: str
    errore: str = ""

    @property
    def ok(self) -> bool:
        return self.codice == 0


def esegui(
    cmd: list[str],
    timeout: float = 30,
    *,
    entrata: str | None = None,
    ambiente: dict[str, str] | None = None,
    cartella: str | None = None,
) -> Uscita:
    """Esegue un comando senza mai sollevare eccezioni: 127 se non esiste, 124 se va in timeout."""
    try:
        r = subprocess.run(
            cmd,
            input=entrata,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=ambiente,
            cwd=cartella,
        )
    except FileNotFoundError:
        return Uscita(127, "", f"{cmd[0]}: comando non trovato")
    except subprocess.TimeoutExpired:
        return Uscita(124, "", f"{cmd[0]}: nessuna risposta in {timeout:.0f} s")
    except OSError as exc:
        return Uscita(1, "", str(exc))
    return Uscita(r.returncode, r.stdout or "", r.stderr or "")


def presente(comando: str) -> bool:
    return shutil.which(comando) is not None or any(
        Path(d, comando).exists() for d in ("/usr/sbin", "/sbin", "/usr/local/sbin")
    )


def leggi(percorso: str | Path) -> str:
    try:
        return Path(percorso).read_text(errors="replace")
    except OSError:
        return ""


def coda(percorso: str | Path, righe: int = 20000) -> list[str]:
    """Le ultime ``righe`` di un file di log (senza leggerlo tutto)."""
    p = Path(percorso)
    try:
        with p.open("rb") as f:
            f.seek(0, os.SEEK_END)
            dimensione = f.tell()
            f.seek(max(dimensione - righe * 300, 0))
            return f.read().decode(errors="replace").splitlines()[-righe:]
    except OSError:
        return []


def con_timeout(funzione: Any, secondi: float, predefinito: Any = None) -> Any:
    """Chiama ``funzione()`` con un limite di tempo (per le risoluzioni DNS, che non ne hanno uno)."""
    ex = ThreadPoolExecutor(max_workers=1)
    futuro = ex.submit(funzione)
    try:
        return futuro.result(timeout=secondi)
    except (Scaduto, Exception):
        return predefinito
    finally:
        ex.shutdown(wait=False)  # una risoluzione DNS bloccata non deve fermare la diagnostica


# --------------------------------------------------------------------------------------------- systemd
def systemctl_show(unita: str, *proprieta: str) -> dict[str, str]:
    r = esegui(["systemctl", "show", unita, *(f"-p{p}" for p in proprieta)], timeout=15)
    return dict(riga.split("=", 1) for riga in r.testo.splitlines() if "=" in riga)


def servizio(unita: str) -> dict[str, str] | None:
    """Stato di un servizio systemd, o None se non è installato."""
    if not presente("systemctl"):
        return None
    s = systemctl_show(unita, "LoadState", "ActiveState", "SubState", "NRestarts", "ActiveEnterTimestamp")
    if s.get("LoadState") in (None, "not-found"):
        return None
    return s


def primo_servizio(*nomi: str) -> tuple[str, dict[str, str]] | None:
    for n in nomi:
        s = servizio(n)
        if s is not None:
            return n, s
    return None


def esito_servizio(nome: str, unita: str, stato: dict[str, str], gruppo: str) -> Esito:
    riavvii = int(stato.get("NRestarts") or 0)
    dettaglio = f"{unita}: {stato.get('ActiveState')} ({stato.get('SubState')})"
    if riavvii:
        dettaglio += f", {riavvii} riavvii automatici"
    if stato.get("ActiveState") != "active":
        return Esito(nome, ERRORE, dettaglio + f" — systemctl status {unita}", gruppo)
    return Esito(nome, ATTENZIONE if riavvii > 5 else OK, dettaglio, gruppo)


def unita_attive() -> list[str]:
    r = esegui(["systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--plain"], 20)
    return [riga.split()[0] for riga in r.testo.splitlines() if riga.strip()]


# --------------------------------------------------------------------------------------------- rete
@dataclass
class Porta:
    protocollo: str
    indirizzo: str
    porta: int
    processo: str = ""

    @property
    def pubblica(self) -> bool:
        """In ascolto su tutti gli indirizzi (o su uno pubblico), non solo in locale o sulle reti di Docker."""
        a = self.indirizzo.strip("[]").split("%")[0]
        if a in ("*", "0.0.0.0", "::"):
            return True
        if a.startswith(("127.", "::1", "fe80:", "169.254.")) or a == "localhost":
            return False
        try:
            import ipaddress

            return not ipaddress.ip_address(a).is_private
        except ValueError:
            return False


def leggi_ss(testo: str) -> list[Porta]:
    """Uscita di ``ss -H -tulpn`` → porte in ascolto."""
    porte = []
    for riga in testo.splitlines():
        parti = riga.split()
        if len(parti) < 5 or parti[0] not in ("tcp", "udp"):
            continue
        if parti[0] == "tcp" and parti[1] != "LISTEN":
            continue
        locale = parti[4]
        indirizzo, _, numero = locale.rpartition(":")
        if not numero.isdigit():
            continue
        m = re.search(r'users:\(\("([^"]+)"', riga)
        porte.append(Porta(parti[0], indirizzo or "*", int(numero), m.group(1) if m else ""))
    return porte


def leggi_proc_net(testo: str, protocollo: str, v6: bool) -> list[Porta]:
    """/proc/net/tcp(6): le porte in ascolto (stato 0A), senza il nome del processo."""
    porte = []
    for riga in testo.splitlines()[1:]:
        p = riga.split()
        if len(p) < 4 or (protocollo == "tcp" and p[3] != "0A"):
            continue
        indirizzo, numero = p[1].split(":")
        if v6:
            tutti = set(indirizzo) == {"0"}
            locale = indirizzo == "00000000000000000000000001000000"
            testo_ind = "::" if tutti else "::1" if locale else "::ffff"
        else:
            testo_ind = ".".join(str(int(indirizzo[i : i + 2], 16)) for i in (6, 4, 2, 0))
        porte.append(Porta(protocollo, testo_ind, int(numero, 16)))
    return porte


def porte_in_ascolto() -> list[Porta]:
    if presente("ss"):
        return leggi_ss(esegui(["ss", "-H", "-tulpn"], timeout=15).testo)
    porte = []
    for nome, v6 in (("tcp", False), ("tcp6", True)):
        porte += leggi_proc_net(leggi(f"/proc/net/{nome}"), "tcp", v6)
    return porte


def ip_pubblico_locale() -> str | None:
    """L'indirizzo con cui il server esce verso Internet (quello della scheda di rete, senza servizi esterni)."""
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # nessun pacchetto parte: serve solo a scegliere l'interfaccia
        return s.getsockname()[0]
    return None


# --------------------------------------------------------------------------------------------- certificati
def scadenza_file_certificato(percorso: str | Path) -> dt.datetime | None:
    try:
        dati = ssl._ssl._test_decode_cert(str(percorso))  # type: ignore[attr-defined]
        return dt.datetime.fromtimestamp(ssl.cert_time_to_seconds(dati["notAfter"]), dt.timezone.utc)  # noqa: UP017
    except Exception:
        r = esegui(["openssl", "x509", "-enddate", "-noout", "-in", str(percorso)], timeout=10)
        m = re.search(r"notAfter=(.+)", r.testo)
        if not m:
            return None
        with contextlib.suppress(ValueError):
            return dt.datetime.fromtimestamp(ssl.cert_time_to_seconds(m.group(1).strip()), dt.timezone.utc)  # noqa: UP017
    return None


def certificato_remoto(
    host: str, porta: int, starttls: str | None = None, timeout: float = 10
) -> tuple[int | None, str]:
    """(giorni alla scadenza, problema) del certificato presentato da host:porta, verificato come farebbe un
    client. ``starttls``: "smtp" o "imap" per le porte che passano a TLS dopo il saluto."""
    contesto = ssl.create_default_context()
    try:
        if starttls == "smtp":
            import smtplib

            with smtplib.SMTP(host, porta, timeout=timeout) as s:
                s.ehlo()
                s.starttls(context=contesto)
                cert = s.sock.getpeercert()  # type: ignore[union-attr]
        elif starttls == "imap":
            import imaplib

            with imaplib.IMAP4(host, porta, timeout=timeout) as s:
                s.starttls(ssl_context=contesto)
                cert = s.sock.getpeercert()
        else:
            with (
                socket.create_connection((host, porta), timeout=timeout) as grezzo,
                contesto.wrap_socket(grezzo, server_hostname=host) as s,
            ):
                cert = s.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        return None, f"certificato non valido ({exc.verify_message})"
    except (OSError, ssl.SSLError) as exc:
        return None, f"connessione TLS non riuscita ({exc})"
    except Exception as exc:
        return None, f"TLS non disponibile ({exc})"
    scadenza = ssl.cert_time_to_seconds(cert["notAfter"])
    return int((scadenza - time.time()) // 86400), ""


def livello_giorni(giorni: int | None, errore: int = 7, attenzione: int = 21) -> str:
    if giorni is None or giorni < errore:
        return ERRORE
    return ATTENZIONE if giorni < attenzione else OK


# --------------------------------------------------------------------------------------------- fine supporto
def fine_supporto(fine: str | None, oggi: dt.date, cosa: str) -> tuple[str, str]:
    """(livello, testo) per una data di fine supporto «AAAA-MM» (o None se non nota)."""
    if not fine:
        return OK, ""
    anno, mese = (int(x) for x in fine.split("-"))
    data = dt.date(anno, mese, 28)
    if data < oggi:
        return ERRORE, f"{cosa} non riceve più aggiornamenti di sicurezza dal {mese:02d}/{anno}"
    if (data - oggi).days < 180:
        return ATTENZIONE, f"{cosa}: fine del supporto a {mese:02d}/{anno}, pianifica l'aggiornamento"
    return OK, f"supportato fino al {mese:02d}/{anno}"
