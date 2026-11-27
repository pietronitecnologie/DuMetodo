"""Area «web»: nginx o Apache, configurazione, siti (risposta, redirect https, intestazioni di sicurezza),
certificati (file di Let's Encrypt e quelli presentati dai siti), rinnovo automatico, errori 5xx nei log."""

from __future__ import annotations

import datetime as dt
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .base import (
    ATTENZIONE,
    ERRORE,
    OK,
    SERVE_ROOT,
    Contesto,
    Esito,
    certificato_remoto,
    coda,
    esegui,
    esito_servizio,
    livello_giorni,
    presente,
    primo_servizio,
    scadenza_file_certificato,
    servizio,
)

G = "web"
INTESTAZIONI = {
    "strict-transport-security": "HSTS",
    "x-content-type-options": "X-Content-Type-Options",
}
MASSIMO_SITI = 15
_DATA_LOG = re.compile(r"\[(\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4})\]\s+\"[^\"]*\"\s+(\d{3})")


def presente_area(ctx: Contesto) -> bool:
    return presente("nginx") or presente("apache2") or Path("/etc/letsencrypt/live").exists()


def nomi_server(config: str) -> list[str]:
    """I server_name dei blocchi in ascolto su 443 nella configurazione completa (nginx -T)."""
    nomi: list[str] = []
    for blocco in re.split(r"\bserver\s*\{", config)[1:]:
        if not re.search(r"listen\s+[^;]*443", blocco):
            continue
        m = re.search(r"server_name\s+([^;]+);", blocco)
        for n in m.group(1).split() if m else []:
            if n not in nomi and n != "_" and "*" not in n and "." in n and not n.startswith("~") and n != "localhost":
                nomi.append(n)
    return nomi


def errori_5xx(righe: list[str], adesso: dt.datetime, minuti: int = 60) -> tuple[int, int]:
    """(richieste, risposte 5xx) dell'ultima ora in un access log in formato combined."""
    totale = errori = 0
    limite = adesso - dt.timedelta(minutes=minuti)
    for r in righe:
        m = _DATA_LOG.search(r)
        if not m:
            continue
        try:
            quando = dt.datetime.strptime(m.group(1), "%d/%b/%Y:%H:%M:%S %z")
        except ValueError:
            continue
        if quando < limite:
            continue
        totale += 1
        errori += m.group(2).startswith("5")
    return totale, errori


class _SenzaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # type: ignore[no-untyped-def]
        return None


def controlla_sito(url: str) -> list[Esito]:
    e = []
    parti = urlsplit(url)
    host = parti.hostname or ""
    inizio = time.monotonic()
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "MetodoConsole"}), timeout=15)  # noqa: S310
        codice, intestazioni = r.status, {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as exc:
        codice, intestazioni = exc.code, {k.lower(): v for k, v in exc.headers.items()}
    except Exception as exc:
        return [Esito(f"Sito {host}", ERRORE, f"{url} non risponde ({exc})", G)]
    secondi = time.monotonic() - inizio
    livello = ERRORE if codice >= 500 else ATTENZIONE if secondi > 5 else OK
    dettaglio = f"{url} risponde {codice} in {secondi:.1f} s"
    mancanti = (
        [nome for chiave, nome in INTESTAZIONI.items() if chiave not in intestazioni] if parti.scheme == "https" else []
    )
    if mancanti and livello == OK:
        livello = ATTENZIONE
    if mancanti:
        dettaglio += f"; mancano le intestazioni {', '.join(mancanti)}"
    if "server" in intestazioni and re.search(r"/\d", intestazioni["server"]):
        dettaglio += f"; mostra la versione del server ({intestazioni['server']}: server_tokens off)"
        livello = ATTENZIONE if livello == OK else livello
    e.append(Esito(f"Sito {host}", livello, dettaglio, G))
    if parti.scheme == "https":
        giorni, problema = certificato_remoto(host, parti.port or 443)
        e.append(Esito(f"Certificato {host}", livello_giorni(giorni), problema or f"scade tra {giorni} giorni", G))
        apri = urllib.request.build_opener(_SenzaRedirect)
        try:
            apri.open(f"http://{host}/", timeout=10)
            e.append(Esito(f"Redirect https {host}", ATTENZIONE, "http:// risponde senza portare a https://", G))
        except urllib.error.HTTPError as exc:
            dove = exc.headers.get("Location", "")
            ok = exc.code in (301, 302, 307, 308) and dove.startswith("https://")
            e.append(
                Esito(
                    f"Redirect https {host}",
                    OK if ok else ATTENZIONE,
                    f"http:// → {dove}" if ok else f"http:// risponde {exc.code}",
                    G,
                )
            )
        except Exception:
            pass  # porta 80 chiusa: va bene
    return e


def controlli(ctx: Contesto) -> list[Esito]:
    e: list[Esito] = []
    agg = e.append
    config = ""
    trovato = primo_servizio("nginx", "apache2", "httpd")
    if trovato:
        unita, stato = trovato
        agg(esito_servizio("Server web", unita, stato, G))
    if presente("nginx"):
        r = esegui(["nginx", "-t"], timeout=20)
        testo = (r.errore + r.testo).strip()
        if "Permission denied" in testo and not ctx.root:
            agg(Esito("nginx: configurazione", OK, SERVE_ROOT, G))
        else:
            agg(
                Esito(
                    "nginx: configurazione",
                    OK if r.ok else ERRORE,
                    "corretta" if r.ok else testo.splitlines()[-1][:200],
                    G,
                )
            )
            config = esegui(["nginx", "-T"], timeout=20).testo
            if config:
                tokens = re.findall(r"^\s*server_tokens\s+(\w+)", config, re.M)
                agg(
                    Esito(
                        "nginx: versione nascosta",
                        OK if "off" in tokens else ATTENZIONE,
                        "server_tokens off"
                        if "off" in tokens
                        else "le pagine mostrano la versione di nginx: server_tokens off; in nginx.conf",
                        G,
                    )
                )
                vecchi = sorted(
                    {
                        p
                        for m in re.findall(r"ssl_protocols\s+([^;]+);", config)
                        for p in m.split()
                        if p in ("SSLv3", "TLSv1", "TLSv1.1")
                    }
                )
                agg(
                    Esito(
                        "nginx: protocolli TLS",
                        ATTENZIONE if vecchi else OK,
                        f"ammessi protocolli insicuri: {', '.join(vecchi)} (lascia TLSv1.2 TLSv1.3)"
                        if vecchi
                        else "solo protocolli sicuri",
                        G,
                    )
                )
        log = Path("/var/log/nginx/access.log")
        if log.exists():
            totale, errori = errori_5xx(coda(log), dt.datetime.now(dt.timezone.utc))  # noqa: UP017
            livello = ERRORE if totale and errori / totale > 0.05 else ATTENZIONE if errori else OK
            agg(
                Esito(
                    "Errori 5xx (ultima ora)",
                    livello,
                    f"{errori} su {totale} richieste" + (" — /var/log/nginx/error.log" if errori else ""),
                    G,
                )
            )
    elif presente("apache2ctl"):
        r = esegui(["apache2ctl", "-t"], timeout=20)
        agg(
            Esito(
                "Apache: configurazione",
                OK if r.ok else ERRORE,
                "corretta" if r.ok else (r.errore or r.testo).strip()[-200:],
                G,
            )
        )

    # Certificati di Let's Encrypt (file) e rinnovo automatico
    live = Path("/etc/letsencrypt/live")
    certificati = sorted(live.glob("*/cert.pem")) if live.exists() else []
    if live.exists() and not certificati and not ctx.root:
        agg(Esito("Certificati Let's Encrypt", OK, SERVE_ROOT, G))
    for cert in certificati:
        scadenza = scadenza_file_certificato(cert)
        giorni = (scadenza - dt.datetime.now(dt.timezone.utc)).days if scadenza else None  # noqa: UP017
        agg(
            Esito(
                f"Certificato {cert.parent.name}",
                livello_giorni(giorni, 7, 15),
                f"scade tra {giorni} giorni ({scadenza:%d/%m/%Y})" if scadenza else "illeggibile",
                G,
            )
        )
    if certificati:
        rinnovo = servizio("certbot.timer") or servizio("snap.certbot.renew.timer")
        attivo = bool(rinnovo and rinnovo.get("ActiveState") == "active")
        agg(
            Esito(
                "Rinnovo certificati",
                OK if attivo else ERRORE,
                "certbot.timer attivo"
                if attivo
                else "nessun rinnovo automatico: sudo systemctl enable --now certbot.timer",
                G,
            )
        )

    # Siti: quelli indicati in [web] url, altrimenti quelli trovati nella configurazione di nginx
    siti = ctx.lista("web", "url") or [f"https://{n}/" for n in nomi_server(config)]
    for url in siti[:MASSIMO_SITI]:
        e += controlla_sito(url)
    return e
