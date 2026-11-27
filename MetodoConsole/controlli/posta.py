"""Area «posta»: Postfix / Exim / Dovecot, coda, relay aperto, porte e certificati TLS, errori nei log,
DNS dei domini (MX, SPF, DMARC, DKIM), nome inverso (PTR) e liste nere dell'indirizzo del server."""

from __future__ import annotations

import datetime as dt
import json
import re
import socket
from pathlib import Path

from .base import (
    ATTENZIONE,
    ERRORE,
    OK,
    SERVE_ROOT,
    Contesto,
    Esito,
    certificato_remoto,
    coda,
    con_timeout,
    esegui,
    esito_servizio,
    ip_pubblico_locale,
    livello_giorni,
    presente,
    primo_servizio,
)

G = "posta"
LISTE_NERE = ("zen.spamhaus.org", "bl.spamcop.net", "b.barracudacentral.org")
LOG_POSTA = ("/var/log/mail.log", "/var/log/maillog")
PORTE_TLS = (
    (25, "smtp", "SMTP (25)"),
    (465, None, "SMTPS (465)"),
    (587, "smtp", "Invio (587)"),
    (993, None, "IMAPS (993)"),
)


def presente_area(ctx: Contesto) -> bool:
    return bool(
        presente("postconf")
        or presente("exim")
        or presente("exim4")
        or presente("dovecot")
        or ctx.lista("posta", "domini")
    )


# --------------------------------------------------------------------------------------------- lettura
def leggi_postconf(testo: str) -> dict[str, str]:
    return {k.strip(): v.strip() for k, _, v in (r.partition("=") for r in testo.splitlines()) if _}


def relay_aperto(conf: dict[str, str]) -> str:
    """Motivo per cui Postfix farebbe da relay aperto ('' se non lo fa)."""
    reti = conf.get("mynetworks", "")
    if re.search(r"(^|[\s,])0\.0\.0\.0/0|(^|[\s,])::/0", reti):
        return f"mynetworks comprende tutta Internet ({reti})"
    regole = conf.get("smtpd_relay_restrictions", "") + " " + conf.get("smtpd_recipient_restrictions", "")
    if (conf.get("smtpd_relay_restrictions") or conf.get("smtpd_recipient_restrictions")) and not re.search(
        r"(reject|defer)_unauth_destination", regole
    ):
        return "nelle restrizioni manca reject_unauth_destination"
    return ""


def leggi_coda_postfix(testo: str, adesso: float) -> tuple[int, int, float]:
    """``postqueue -j`` → (messaggi, rinviati, ore del più vecchio)."""
    messaggi = rinviati = 0
    vecchio = adesso
    for riga in testo.splitlines():
        try:
            m = json.loads(riga)
        except ValueError:
            continue
        messaggi += 1
        rinviati += m.get("queue_name") == "deferred"
        vecchio = min(vecchio, m.get("arrival_time", adesso))
    return messaggi, rinviati, (adesso - vecchio) / 3600


def leggi_mailq(testo: str) -> int:
    m = re.search(r"in (\d+) Requests?", testo)
    return int(m.group(1)) if m else 0


def eventi_log(righe: list[str], oggi: dt.date) -> dict[str, int]:
    """Conteggi delle ultime 24 ore (circa: le righe con la data di oggi o di ieri) dal log della posta."""
    giorni = {f"{d:%b} {d.day:>2}" for d in (oggi, oggi - dt.timedelta(days=1))} | {
        d.isoformat() for d in (oggi, oggi - dt.timedelta(days=1))
    }
    c = {"rimbalzati": 0, "rinviati": 0, "accessi falliti": 0, "inviati": 0}
    for r in righe:
        if not any(r.startswith(g) for g in giorni):
            continue
        c["rimbalzati"] += "status=bounced" in r
        c["rinviati"] += "status=deferred" in r
        c["inviati"] += "status=sent" in r
        c["accessi falliti"] += bool(re.search(r"SASL \w+ authentication failed|auth failed", r))
    return c


def dns(nome: str, tipo: str) -> list[str] | None:
    """Record DNS (None se non c'è uno strumento per chiederli)."""
    if presente("dig"):
        r = esegui(["dig", "+short", "+time=3", "+tries=2", tipo, nome], timeout=15)
        return [x.strip().strip('"').replace('" "', "") for x in r.testo.splitlines() if x.strip()]
    if presente("host"):
        r = esegui(["host", "-t", tipo, nome], timeout=15)
        if tipo == "TXT":
            return [m.replace('" "', "") for m in re.findall(r'"(.*)"', r.testo)]
        return [x.split()[-1] for x in r.testo.splitlines() if " mail is handled by " in x or " has address " in x]
    return None


def in_lista_nera(ip: str, zona: str) -> str | None:
    """'elencato', 'pulito' o None (non verificabile: es. resolver pubblico rifiutato dalla lista)."""
    nome = ".".join(reversed(ip.split("."))) + "." + zona
    risposta = con_timeout(lambda: socket.gethostbyname(nome), 8, predefinito="")
    if risposta == "":
        return "pulito"
    if risposta.startswith("127.255.255.") or not risposta.startswith("127."):
        return None
    return "elencato"


# --------------------------------------------------------------------------------------------- controlli
def controlli(ctx: Contesto) -> list[Esito]:
    e: list[Esito] = []
    agg = e.append
    postfix = presente("postconf")

    trovato = primo_servizio("postfix", "exim4", "exim", "sendmail")
    if trovato:
        agg(esito_servizio("Server di posta (MTA)", trovato[0], trovato[1], G))
    trovato = primo_servizio("dovecot")
    if trovato:
        agg(esito_servizio("Caselle (IMAP/POP)", *trovato, G))
    for nome, etichetta in (
        ("opendkim", "Firma DKIM"),
        ("rspamd", "Antispam (rspamd)"),
        ("spamassassin", "Antispam"),
        ("clamav-daemon", "Antivirus posta"),
    ):
        trovato = primo_servizio(nome)
        if trovato:
            agg(esito_servizio(etichetta, *trovato, G))

    conf: dict[str, str] = {}
    if postfix:
        conf = leggi_postconf(esegui(["postconf"], timeout=20).testo)
        motivo = relay_aperto(conf)
        agg(
            Esito(
                "Relay aperto",
                ERRORE if motivo else OK,
                f"chiunque può spedire posta dal server: {motivo}"
                if motivo
                else "no: inoltra solo per i propri utenti e reti",
                G,
            )
        )
        r = esegui(["postqueue", "-j"], timeout=30)
        if r.ok:
            import time

            messaggi, rinviati, ore = leggi_coda_postfix(r.testo, time.time())
            livello = ERRORE if messaggi > 500 else ATTENZIONE if messaggi > 50 or (messaggi and ore > 24) else OK
            dettaglio = f"{messaggi} messaggi ({rinviati} rinviati)" + (
                f", il più vecchio da {ore:.0f} ore" if messaggi else ""
            )
            agg(Esito("Coda della posta", livello, dettaglio + (" — postqueue -p" if livello != OK else ""), G))
        elif "permission" in (r.errore + r.testo).lower() and not ctx.root:
            agg(Esito("Coda della posta", OK, SERVE_ROOT, G))
    elif presente("exim") or presente("exim4"):
        r = esegui([("exim4" if presente("exim4") else "exim"), "-bpc"], timeout=30)
        if r.ok and r.testo.strip().isdigit():
            n = int(r.testo.strip())
            agg(Esito("Coda della posta", ERRORE if n > 500 else ATTENZIONE if n > 50 else OK, f"{n} messaggi", G))

    # Log degli ultimi giorni
    log = next((Path(p) for p in LOG_POSTA if Path(p).exists()), None)
    if log is not None:
        c = eventi_log(coda(log, 50000), ctx.oggi)
        livello = ATTENZIONE if c["rimbalzati"] > 20 or c["rinviati"] > 100 else OK
        agg(
            Esito(
                "Consegne (oggi e ieri)",
                livello,
                f"{c['inviati']} inviati, {c['rinviati']} rinviati, {c['rimbalzati']} rimbalzati",
                G,
            )
        )
        if c["accessi falliti"]:
            agg(
                Esito(
                    "Accessi falliti alla posta",
                    ATTENZIONE if c["accessi falliti"] > 200 else OK,
                    f"{c['accessi falliti']} tentativi (fail2ban per postfix/dovecot li blocca)",
                    G,
                )
            )

    # Porte e certificati TLS
    host = ctx.valore("posta", "host") or conf.get("myhostname") or socket.getfqdn()
    in_ascolto = {p.porta for p in ctx.porte()}
    for porta, starttls, etichetta in PORTE_TLS:
        if porta not in in_ascolto:
            continue
        giorni, problema = certificato_remoto(host, porta, starttls)
        agg(
            Esito(
                f"TLS {etichetta}",
                livello_giorni(giorni) if not problema else ATTENZIONE,
                problema or f"certificato di {host}, scade tra {giorni} giorni",
                G,
            )
        )

    # DNS dei domini
    domini = ctx.lista("posta", "domini") or (
        [conf["mydomain"]]
        if conf.get("mydomain") and "." in conf["mydomain"] and not conf["mydomain"].endswith(".localdomain")
        else []
    )
    selettori = ctx.lista("posta", "dkim_selettori", "default dkim mail")
    for dominio in domini[:10]:
        mx = dns(dominio, "MX")
        if mx is None:
            agg(Esito(f"DNS {dominio}", ATTENZIONE, "manca dig per controllare i record: sudo apt install dnsutils", G))
            break
        agg(
            Esito(
                f"MX {dominio}",
                OK if mx else ERRORE,
                ", ".join(mx[:3]) if mx else "nessun record MX: la posta per il dominio non arriva",
                G,
            )
        )
        spf = [t for t in (dns(dominio, "TXT") or []) if t.lower().startswith("v=spf1")]
        if len(spf) > 1:
            agg(Esito(f"SPF {dominio}", ERRORE, f"{len(spf)} record SPF: ne va lasciato uno solo", G))
        elif not spf:
            agg(Esito(f"SPF {dominio}", ATTENZIONE, "nessun record SPF: la posta rischia di finire nello spam", G))
        else:
            permissivo = re.search(r"[+?]all\b", spf[0])
            agg(
                Esito(
                    f"SPF {dominio}",
                    ERRORE if permissivo else OK,
                    ("chiunque può spedire a nome del dominio: " if permissivo else "") + spf[0][:120],
                    G,
                )
            )
        dmarc = [t for t in (dns(f"_dmarc.{dominio}", "TXT") or []) if t.lower().startswith("v=dmarc1")]
        if not dmarc:
            agg(Esito(f"DMARC {dominio}", ATTENZIONE, f"nessun record _dmarc.{dominio}", G))
        else:
            politica = re.search(r"\bp=(\w+)", dmarc[0])
            agg(Esito(f"DMARC {dominio}", OK, f"politica {politica.group(1) if politica else '?'}", G))
        trovati = [
            s
            for s in selettori
            if any("v=dkim1" in t.lower() or "p=" in t for t in (dns(f"{s}._domainkey.{dominio}", "TXT") or []))
        ]
        agg(
            Esito(
                f"DKIM {dominio}",
                OK if trovati else ATTENZIONE,
                f"selettore {', '.join(trovati)}"
                if trovati
                else f"nessuna chiave per i selettori {', '.join(selettori)} ([posta] dkim_selettori)",
                G,
            )
        )

    # Indirizzo del server: nome inverso e liste nere (solo se il server spedisce posta)
    if postfix or presente("exim4"):
        ip = ctx.valore("posta", "ip") or ip_pubblico_locale()
        import ipaddress

        if ip and not ipaddress.ip_address(ip).is_private:
            inverso = con_timeout(lambda: socket.gethostbyaddr(ip)[0], 8, predefinito="")
            diretto = con_timeout(lambda: socket.gethostbyname(inverso), 8, predefinito="") if inverso else ""
            if not inverso:
                agg(
                    Esito(
                        "Nome inverso (PTR)",
                        ERRORE,
                        f"{ip} non ha un nome inverso: molti server rifiutano la posta (chiedilo al provider)",
                        G,
                    )
                )
            else:
                agg(
                    Esito(
                        "Nome inverso (PTR)",
                        OK if diretto == ip else ATTENZIONE,
                        f"{ip} → {inverso}"
                        + ("" if diretto == ip else f" (che però punta a {diretto or 'nessun indirizzo'})"),
                        G,
                    )
                )
            elencato, sconosciute = [], []
            for zona in LISTE_NERE:
                stato = in_lista_nera(ip, zona)
                if stato == "elencato":
                    elencato.append(zona)
                elif stato is None:
                    sconosciute.append(zona)
            if elencato:
                agg(Esito("Liste nere", ERRORE, f"{ip} è in {', '.join(elencato)}: la posta viene rifiutata", G))
            else:
                agg(
                    Esito(
                        "Liste nere",
                        OK,
                        f"{ip} non risulta in {', '.join(z for z in LISTE_NERE if z not in sconosciute)}"
                        + (f" ({', '.join(sconosciute)} non verificabili da qui)" if sconosciute else ""),
                        G,
                    )
                )
        elif ip:
            agg(
                Esito(
                    "Indirizzo del server",
                    OK,
                    f"{ip} è privato (dietro NAT): PTR e liste nere vanno controllati "
                    "sull'indirizzo pubblico ([posta] ip)",
                    G,
                )
            )
    return e
