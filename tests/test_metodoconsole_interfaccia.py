"""Interfaccia di MetodoConsole (Textual): letture del server e schede, senza Docker né root."""

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

CARTELLA = Path(__file__).resolve().parent.parent / "MetodoConsole"
if str(CARTELLA) not in sys.path:
    sys.path.insert(0, str(CARTELLA))

from tui import sistema as s  # noqa: E402

PS = "\n".join(
    json.dumps(x)
    for x in (
        {
            "ID": "aaa111",
            "Names": "metodo-web-1",
            "Image": "metodo:locale",
            "State": "running",
            "Status": "Up 3 hours (healthy)",
            "Ports": "0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp",
            "Labels": "com.docker.compose.project=metodo,com.docker.compose.service=web",
        },
        {
            "ID": "bbb222",
            "Names": "metodo-migrate-1",
            "Image": "metodo:locale",
            "State": "exited",
            "Status": "Exited (1) 2 minutes ago",
            "Labels": "com.docker.compose.project=metodo,com.docker.compose.service=migrate",
        },
        {
            "ID": "ccc333",
            "Names": "metodo-db-1",
            "Image": "postgres:16",
            "State": "running",
            "Status": "Up 3 hours (health: starting)",
            "Labels": "com.docker.compose.project=metodo,com.docker.compose.service=db",
        },
        {
            "ID": "ddd444",
            "Names": "dumetodo-web-1",
            "Image": "dumetodo:locale",
            "State": "running",
            "Status": "Up 5 days",
            "Ports": "0.0.0.0:8000->8000/tcp",
            "Labels": "com.docker.compose.project=dumetodo,com.docker.compose.service=web",
        },
    )
)


# ----------------------------------------------------------------------------------------- docker
def test_container_stato_ordine_e_porte():
    elenco = s.ordina(s.leggi_container(PS), "metodo")
    assert [c.nome for c in elenco] == ["metodo-db-1", "metodo-migrate-1", "metodo-web-1", "dumetodo-web-1"]
    db, migrate, web, _ = elenco
    assert [s.stato(c) for c in elenco] == [s.AVVIO, s.ERRORE, s.OK, s.OK]
    assert migrate.codice_uscita == 1 and web.salute == "healthy" and db.salute == "starting"
    assert s.porte(web) == "8000→8000"
    testo = s.riepilogo(elenco, "metodo")
    assert "1/3 attivi" in testo and "altri container accesi: 1 (dumetodo-web-1 su 8000→8000)" in testo
    assert len(s.leggi_container("[" + ",".join(PS.splitlines()) + "]")) == 4
    s.unisci_statistiche(elenco, '{"Name": "metodo-web-1", "CPUPerc": "3.2%", "MemUsage": "180MiB / 7.6GiB"}\nx')
    assert (web.cpu, web.memoria) == ("3.2%", "180MiB")
    assert s.righe_di_errore(["ok", "Traceback (x)", "a ERROR b"]) == ["Traceback (x)", "a ERROR b"]
    assert s.filtra(["Avvio", "fine"], "avv") == ["Avvio"]
    assert s.leggi_df('{"Type":"Images","Size":"2GB"}\nrotto') == [{"Type": "Images", "Size": "2GB"}]


# ----------------------------------------------------------------------------------------- risorse
def test_risorse_dischi_e_testi(tmp_path):
    m = s.leggi_meminfo("MemTotal:       8000000 kB\nMemAvailable:   2000000 kB\nHugePages_Total: 0\n")
    assert m == {"MemTotal": 8_192_000_000, "MemAvailable": 2_048_000_000, "HugePages_Total": 0}
    r = s.Risorse(memoria_totale=100, memoria_disponibile=25)
    assert r.memoria_usata == 0.75 and s.Risorse().memoria_usata == 0
    dischi = s.leggi_dischi([("uno", str(tmp_path)), ("due", str(tmp_path / "non-esiste" / "x"))])
    assert len(dischi) == 1 and dischi[0].etichetta == "uno, due" and 0 <= dischi[0].frazione <= 1
    assert s.barra(0.5, 10) == "█████░░░░░" and s.barra(2, 4) == "████"
    assert (s.durata(90061), s.durata(3720), s.durata(300)) == ("1 g 1 h", "1 h 2 min", "5 min")
    assert s.leggi_systemd("ActiveState=active\nResult=success\nvuota") == {
        "ActiveState": "active",
        "Result": "success",
    }
    assert s._data_systemd("Wed 2026-11-25 02:30:00 CET") == "25/11 02:30" and s._data_systemd("n/a") == ""
    assert s.leggi_du(f"100\t{tmp_path}/a\n900\t{tmp_path}/b\n1000\t{tmp_path}\n") == [
        (900, f"{tmp_path}/b"),
        (100, f"{tmp_path}/a"),
    ]
    testo = "Archived and active journals take up 1.2G in the file system."
    assert s.leggi_journal_spazio(testo) == "1.2GB"


# ----------------------------------------------------------------------------------------- sicurezza
def test_firewall_antivirus_accessi_aggiornamenti_porte():
    ufw = (
        "Status: active\n\n     To                         Action      From\n"
        "[ 1] 22/tcp                     ALLOW IN    Anywhere\n[ 2] 443/tcp ALLOW IN Anywhere\n"
    )
    assert s.leggi_ufw(ufw) == (
        True,
        ["[ 1] 22/tcp                     ALLOW IN    Anywhere", "[ 2] 443/tcp ALLOW IN Anywhere"],
    )
    assert s.leggi_ufw("Status: inactive") == (False, [])
    nft = (
        "table inet filter {\n chain input {\n  type filter hook input priority 0; policy drop;\n"
        "  tcp dport 22 accept\n }\n}"
    )
    attivo, regole = s.leggi_nft(nft)
    assert attivo and "tcp dport 22 accept" in regole

    assert s.leggi_versione_clamav("ClamAV 1.0.7/27470/Mon Nov 24 09:35:02 2025") == (
        "1.0.7",
        "Mon Nov 24 09:35:02 2025",
    )
    import datetime as dt

    assert s.eta_firme("Mon Nov 24 09:35:02 2025", dt.datetime(2025, 11, 30)) == 5
    log = (
        "/tmp/x.exe: Win.Test.EICAR_HDB-1 FOUND\n----------- SCAN SUMMARY -----------\n"
        "Scanned files: 120\nInfected files: 1\nTime: 3.2 sec\n"
    )
    r = s.leggi_riepilogo_scansione(log)
    assert r["Infected files"] == "1" and r["Scanned files"] == "120" and "EICAR" in r["trovati"]

    assert (
        s.leggi_sshd("port 2222\npermitrootlogin no\nPasswordAuthentication yes\n")["passwordauthentication"] == "yes"
    )
    assert s.leggi_jail("|- Number of jail: 2\n`- Jail list:\tsshd, recidive") == ["sshd", "recidive"]
    assert s.leggi_fail2ban_jail("   `- Banned IP list:\t203.0.113.5 198.51.100.7") == ["203.0.113.5", "198.51.100.7"]
    totale, ip = s.conta_tentativi(
        "Failed password for root from 203.0.113.5 port 1 ssh2\n"
        "Invalid user admin from 203.0.113.5 port 2\nAccepted publickey for pt from 10.0.0.2\n"
        "Failed password for pt from 198.51.100.7 port 3 ssh2\n"
    )
    assert totale == 3 and ip[0] == ("203.0.113.5", 2)

    apt = (
        "Listing...\nopenssl/stable-security 3.0.15-1 amd64 [upgradable from: 3.0.14-1]\n"
        "curl/stable 7.88 amd64 [upgradable from: 7.87]\n"
    )
    pacchetti = s.leggi_apt_aggiornabili(apt)
    assert pacchetti == [("openssl", "3.0.15-1", True), ("curl", "7.88", False)]
    assert s.Aggiornamenti(pacchetti=pacchetti).sicurezza == 1

    ss = (
        'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n'
        'tcp LISTEN 0 128 [::]:22 [::]:* users:(("sshd",pid=1,fd=4))\n'
        'tcp LISTEN 0 128 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=2,fd=5))\n'
        'tcp LISTEN 0 128 0.0.0.0:6379 0.0.0.0:* users:(("redis-server",pid=3,fd=6))\n'
    )
    porte = s.leggi_ss(ss)
    assert [(p.porta, p.indirizzo, p.processo, p.pubblica) for p in porte] == [
        (22, "0.0.0.0", "sshd", True),
        (22, "::", "sshd", True),
        (5432, "127.0.0.1", "postgres", False),
        (6379, "0.0.0.0", "redis-server", True),
    ]


def test_controlli_di_sicurezza():
    porte = s.leggi_ss('tcp LISTEN 0 1 0.0.0.0:6379 0.0.0.0:* users:(("redis-server",pid=3,fd=6))')
    brutti = s.controlli_sicurezza(
        s.Firewall("ufw", False, gestibile=True),
        s.Antivirus(),
        s.Accessi(ssh={"permitrootlogin": "yes", "passwordauthentication": "yes"}, tentativi_24h=500),
        s.Aggiornamenti(pacchetti=[("openssl", "3", True)], riavvio_richiesto=True),
        porte,
        env_permessi=0o644,
    )
    per_nome = {c.nome: c for c in brutti}
    assert per_nome["Firewall"].livello == s.ERRORE and "Attiva" in per_nome["Firewall"].rimedio
    assert per_nome["SSH: accesso di root"].livello == s.ERRORE
    assert per_nome["Aggiornamenti di sicurezza"].livello == s.ERRORE
    assert per_nome["File .env"].livello == s.ERRORE
    assert "6379/tcp (redis-server)" in per_nome["Porte aperte"].dettaglio
    assert {"Fail2ban", "Antivirus", "Riavvio", "Tentativi di accesso", "Aggiornamenti automatici"} <= set(per_nome)
    assert s.peggiore([c.livello for c in brutti]) == s.ERRORE

    buoni = s.controlli_sicurezza(
        s.Firewall("ufw", True, ["[ 1] 22/tcp ALLOW IN"], gestibile=True),
        s.Antivirus(True, "1.0", "oggi", 0, ultima_scansione={"Infected files": "0"}),
        s.Accessi(ssh={"permitrootlogin": "prohibit-password", "passwordauthentication": "no"}, fail2ban=True),
        s.Aggiornamenti(automatici=True),
        s.leggi_ss('tcp LISTEN 0 1 0.0.0.0:443 0.0.0.0:* users:(("nginx",pid=3,fd=6))'),
        env_permessi=0o600,
    )
    assert all(c.livello == s.OK for c in buoni), [c for c in buoni if c.livello != s.OK]


def test_avvisi_della_panoramica():
    elenco = s.ordina(s.leggi_container(PS), "metodo")
    pieno = s.Disco("sistema", "/", 100, 95)
    timer = [s.Timer("metodoconsole-backup", True, esito="success"), s.Timer("metodoconsole-controllo", False)]
    testi = [t for _, t in s.avvisi(s.Risorse(cpu=2, carico=(9, 9, 9)), [pieno], elenco, "metodo", 40, 26, timer)]
    assert any("Disco quasi pieno" in t for t in testi)
    assert any("Servizio migrate" in t for t in testi)
    assert any("dumetodo-web-1" in t for t in testi)
    assert any("Ultimo backup di 40 ore" in t for t in testi)
    assert any("metodoconsole-controllo spento" in t for t in testi)
    assert any("Server molto carico" in t for t in testi)
    tutto_bene = s.avvisi(
        s.Risorse(), [], elenco[:1], "metodo", 1, 26, [s.Timer(n, True, esito="success") for n in s.TIMER]
    )
    assert tutto_bene == [(s.OK, "Nessun problema rilevato.")]


def test_valori_scritti_dall_utente():
    """Finiscono in comandi ufw/fail2ban: solo forme sicure."""
    assert s.ip_valido("203.0.113.5") and s.ip_valido("2001:db8::1") and s.ip_valido("10.0.0.0/8")
    assert not s.ip_valido("1.2.3.4; rm -rf /") and not s.ip_valido("") and not s.ip_valido("localhost")
    assert s.regola_ufw_valida("443/tcp") and s.regola_ufw_valida("8000") and s.regola_ufw_valida("6000:6010/udp")
    assert (
        not s.regola_ufw_valida("443/tcp && reboot")
        and not s.regola_ufw_valida("70000")
        and not s.regola_ufw_valida("ssh")
    )
    assert s.numero_regola_ufw("[ 3] 22/tcp ALLOW IN") == 3 and s.numero_regola_ufw("altro") is None


# ----------------------------------------------------------------------------------------- interfaccia
@pytest.fixture
def mc():
    spec = importlib.util.spec_from_file_location("metodoconsole", CARTELLA / "metodoconsole.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["metodoconsole"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def test_interfaccia_schede(tmp_path, monkeypatch, mc):
    """L'interfaccia vera, senza terminale (pilota di Textual) e con un «docker» finto: tutte le schede."""
    from textual.widgets import DataTable, Static, TabbedContent
    from tui import app as interfaccia

    ps = tmp_path / "ps.json"
    ps.write_text(PS)
    df = tmp_path / "df.json"
    df.write_text(
        json.dumps({"Type": "Images", "TotalCount": "3", "Active": "2", "Size": "1.2GB", "Reclaimable": "1GB"})
    )
    immagini = tmp_path / "immagini.json"
    immagini.write_text(json.dumps({"Repository": "metodo", "Tag": "locale", "Size": "900MB", "CreatedSince": "ieri"}))
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        f'case "$*" in\n'
        f"  ps*) cat {ps} ;;\n"
        '  *config*) echo \'{"name": "metodo"}\' ;;\n'
        "  info*) echo '27.3.1|/var/lib/docker' ;;\n"
        f"  'system df'*) cat {df} ;;\n"
        f"  images*) cat {immagini} ;;\n"
        "  logs*) echo 'riga di log' ;;\n"
        "esac\n"
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    conf = mc.Conf()
    conf.cartella, conf.backup_cartella = tmp_path, tmp_path / "backup"
    (tmp_path / "compose.yaml").write_text("services: {}\n")

    async def prova() -> None:
        app = interfaccia.MetodoConsole(conf, mc.Compose(conf), mc)
        async with app.run_test(size=(180, 60)) as pilot:
            await pilot.pause(0.5)
            await app.workers.wait_for_complete()
            await pilot.pause(0.2)
            metodo = str(app.query_one("#r-metodo", Static).render())
            assert "web" in metodo and "migrate" in metodo
            avvisi = app.query_one("#avvisi", DataTable)
            testi = [str(avvisi.get_row_at(i)[1]) for i in range(avvisi.row_count)]
            assert any("dumetodo-web-1" in t for t in testi) and any("Nessun backup" in t for t in testi)
            # tutte le schede, con i tasti 1-8
            for n, nome in enumerate(interfaccia.SCHEDE, 1):
                await pilot.press(str(n))
                await pilot.pause(0.2)
                await app.workers.wait_for_complete()
                assert app.query_one("#schede", TabbedContent).active == nome
            await pilot.press("2")
            await app.workers.wait_for_complete()
            await pilot.pause(0.2)
            tabella = app.query_one("#container", DataTable)
            assert tabella.row_count == 4
            tabella.focus()
            await pilot.press("f")  # fermare chiede conferma
            await pilot.pause(0.2)
            assert type(app.screen).__name__ == "Conferma"
            await pilot.press("escape")
            await pilot.pause(0.1)
            await pilot.press("enter")  # Invio: log del container
            await pilot.pause(0.5)
            await app.workers.wait_for_complete()
            assert type(app.screen).__name__ == "Visore"
            await pilot.press("escape")
            await pilot.press("4")
            await app.workers.wait_for_complete()
            await pilot.pause(0.2)
            assert app.query_one("#controlli", DataTable).row_count > 0
            await pilot.press("q")

    asyncio.run(prova())
