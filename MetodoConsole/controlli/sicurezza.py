"""Area «sicurezza»: firewall, porte esposte, SSH, fail2ban, utenti e sudo, aggiornamenti automatici."""

from __future__ import annotations

import re
from pathlib import Path

from .base import ATTENZIONE, ERRORE, OK, SERVE_ROOT, Contesto, Esito, esegui, leggi, presente, servizio

G = "sicurezza"
# Porte che non dovrebbero mai essere raggiungibili da Internet.
SENSIBILI = {
    5432: "PostgreSQL",
    3306: "MySQL/MariaDB",
    6379: "Redis",
    27017: "MongoDB",
    11211: "Memcached",
    9200: "Elasticsearch",
    2375: "API di Docker senza TLS",
    2376: "API di Docker",
    15672: "RabbitMQ (gestione)",
    5672: "RabbitMQ",
    8000: "server di sviluppo / gunicorn diretto",
}
ATTESE = "22,25,53,80,110,143,443,465,587,993,995"


def presente_area(ctx: Contesto) -> bool:
    return True


def leggi_ufw(testo: str) -> tuple[bool, list[str]]:
    attivo = bool(re.search(r"^Status:\s*active", testo, re.M))
    regole = [r.strip() for r in testo.splitlines() if re.search(r"\bALLOW\b|\bDENY\b|\bLIMIT\b", r)]
    return attivo, regole


def porte_aperte_a_tutti(regole: list[str]) -> set[int]:
    """Porte che il firewall lascia passare da qualunque indirizzo («ALLOW IN Anywhere»)."""
    aperte = set()
    for r in regole:
        m = re.match(r"(\d+)(?::\d+)?(?:/(?:tcp|udp))?\s.*ALLOW(?: IN)?\s+Anywhere", r)
        if m:
            aperte.add(int(m.group(1)))
    return aperte


def leggi_sshd(testo: str) -> dict[str, str]:
    return {p[0].lower(): p[1] for p in (r.split(None, 1) for r in testo.splitlines()) if len(p) == 2}


def controlli(ctx: Contesto) -> list[Esito]:
    e: list[Esito] = []
    agg = e.append

    # Firewall
    aperte_fw: set[int] = set()
    firewall_attivo = None
    if presente("ufw"):
        r = esegui(["ufw", "status"], timeout=15)
        if not ctx.root or "need to be root" in r.errore:
            agg(Esito("Firewall", OK, SERVE_ROOT, G))
        else:
            firewall_attivo, regole = leggi_ufw(r.testo)
            aperte_fw = porte_aperte_a_tutti(regole)
            agg(
                Esito(
                    "Firewall (ufw)",
                    OK if firewall_attivo else ERRORE,
                    f"attivo, {len(regole)} regole"
                    if firewall_attivo
                    else "spento: sudo ufw enable (dopo aver permesso SSH)",
                    G,
                )
            )
    elif presente("nft") and ctx.root:
        r = esegui(["nft", "list", "ruleset"], timeout=15)
        firewall_attivo = "chain" in r.testo
        agg(
            Esito(
                "Firewall (nftables)",
                OK if firewall_attivo else ERRORE,
                "regole presenti" if firewall_attivo else "nessuna regola",
                G,
            )
        )
    else:
        agg(
            Esito(
                "Firewall",
                ATTENZIONE if ctx.root else OK,
                "nessun firewall trovato (consigliato ufw)" if ctx.root else SERVE_ROOT,
                G,
            )
        )

    # Porte in ascolto su tutti gli indirizzi
    attese = {int(p) for p in ctx.lista("diagnostica", "porte_attese", ATTESE) if p.isdigit()}
    pubbliche = [p for p in ctx.porte() if p.pubblica]
    for numero, cosa in sorted(SENSIBILI.items()):
        trovate = [p for p in pubbliche if p.porta == numero]
        if not trovate:
            continue
        aperta = numero in aperte_fw
        processo = trovate[0].processo or cosa
        if aperta or firewall_attivo is False:
            agg(
                Esito(
                    f"Porta {numero} ({cosa})",
                    ERRORE,
                    f"{processo} ascolta su tutti gli indirizzi e il firewall la lascia aperta a Internet: "
                    f"chiudila (sudo ufw delete allow {numero}/tcp) o limita l'ascolto a 127.0.0.1",
                    G,
                )
            )
        else:
            agg(
                Esito(
                    f"Porta {numero} ({cosa})",
                    ATTENZIONE,
                    f"{processo} ascolta su tutti gli indirizzi (il firewall la blocca, ma Docker e regole "
                    "future possono aprirla): meglio solo 127.0.0.1",
                    G,
                )
            )
    strane = sorted(
        {
            (p.porta, p.processo)
            for p in pubbliche
            if p.porta not in attese and p.porta not in SENSIBILI and p.protocollo == "tcp"
        }
    )
    if strane:
        agg(
            Esito(
                "Altre porte esposte",
                ATTENZIONE,
                ", ".join(f"{n} ({pr or '?'})" for n, pr in strane[:12]) + " — attese: [diagnostica] porte_attese",
                G,
            )
        )
    else:
        agg(
            Esito(
                "Porte esposte",
                OK,
                ", ".join(str(n) for n in sorted({p.porta for p in pubbliche if p.protocollo == "tcp"})) or "nessuna",
                G,
            )
        )

    # SSH
    if presente("sshd"):
        r = esegui(["sshd", "-T"], timeout=15)
        if r.ok:
            c = leggi_sshd(r.testo)
            root_login = c.get("permitrootlogin", "?")
            password = c.get("passwordauthentication", "?")
            if root_login == "yes":
                agg(
                    Esito(
                        "SSH: accesso di root",
                        ERRORE,
                        "PermitRootLogin yes: chiunque può provare la password di root (metti «no»)",
                        G,
                    )
                )
            else:
                agg(
                    Esito(
                        "SSH: accesso di root",
                        OK if root_login == "no" else ATTENZIONE,
                        f"PermitRootLogin {root_login}",
                        G,
                    )
                )
            agg(
                Esito(
                    "SSH: password",
                    ATTENZIONE if password == "yes" else OK,
                    "accesso con password attivo: meglio solo chiavi (PasswordAuthentication no)"
                    if password == "yes"
                    else "solo chiavi",
                    G,
                )
            )
        else:
            agg(Esito("SSH", OK, SERVE_ROOT, G))
        s = servizio("fail2ban")
        if s is None:
            agg(
                Esito(
                    "fail2ban",
                    ATTENZIONE,
                    "non installato: blocca chi prova le password (sudo apt install fail2ban)",
                    G,
                )
            )
        else:
            agg(Esito("fail2ban", OK if s.get("ActiveState") == "active" else ATTENZIONE, s.get("ActiveState", "?"), G))
        r = esegui(["journalctl", "-u", "ssh", "-u", "sshd", "--since", "-24h", "--no-pager", "-q"], timeout=30)
        if r.ok:
            tentativi = len(re.findall(r"Failed password|Invalid user|authentication failure", r.testo))
            agg(Esito("SSH: tentativi falliti (24 ore)", ATTENZIONE if tentativi > 1000 else OK, str(tentativi), G))

    # Utenti
    zero = [
        r.split(":")[0]
        for r in leggi("/etc/passwd").splitlines()
        if r.count(":") >= 3 and r.split(":")[2] == "0" and r.split(":")[0] != "root"
    ]
    agg(
        Esito(
            "Utenti con poteri di root",
            ERRORE if zero else OK,
            f"oltre a root: {', '.join(zero)}" if zero else "solo root",
            G,
        )
    )
    if ctx.root:
        vuote = [
            r.split(":")[0] for r in leggi("/etc/shadow").splitlines() if r.count(":") > 1 and r.split(":")[1] == ""
        ]
        agg(Esito("Utenti senza password", ERRORE if vuote else OK, ", ".join(vuote) if vuote else "nessuno", G))
        senza = []
        for f in [Path("/etc/sudoers"), *sorted(Path("/etc/sudoers.d").glob("*"))]:
            for riga in leggi(f).splitlines():
                if "NOPASSWD" in riga and not riga.lstrip().startswith("#"):
                    senza.append(f"{f.name}: {riga.strip()[:60]}")
        agg(
            Esito(
                "sudo senza password",
                ATTENZIONE if senza else OK,
                "; ".join(senza[:4]) if senza else "nessuna regola",
                G,
            )
        )

    # Aggiornamenti automatici
    if presente("apt"):
        auto = leggi("/etc/apt/apt.conf.d/20auto-upgrades")
        attivo = bool(re.search(r'Unattended-Upgrade\s+"1"', auto))
        agg(
            Esito(
                "Aggiornamenti automatici di sicurezza",
                OK if attivo else ATTENZIONE,
                "attivi"
                if attivo
                else "spenti: sudo apt install unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades",
                G,
            )
        )
    return e
