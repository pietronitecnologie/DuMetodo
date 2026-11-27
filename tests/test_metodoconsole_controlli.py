"""MetodoConsole: le aree di diagnostica (sistema, sicurezza, web, posta, database, django, docker).

Si provano le letture delle uscite dei comandi e le decisioni (cosa è un errore, cosa un avviso) con testi di
esempio: niente servizi veri, niente rete."""

import configparser
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

CARTELLA = Path(__file__).resolve().parents[1] / "MetodoConsole"
sys.path.insert(0, str(CARTELLA))

from controlli import AREE, Contesto, base, database, django, docker, posta, registro, sicurezza, sistema, web  # noqa: E402

_spec = importlib.util.spec_from_file_location("metodoconsole", CARTELLA / "metodoconsole.py")
mc = importlib.util.module_from_spec(_spec)
sys.modules["metodoconsole"] = mc  # serve alle dataclass del modulo
_spec.loader.exec_module(mc)


def _ctx(testo=""):
    ini = configparser.ConfigParser()
    ini.read_string(testo)
    return Contesto(ini, oggi=dt.date(2026, 11, 27))


# --------------------------------------------------------------------------------------------- base
def test_porte_da_ss_e_da_proc():
    ss = (
        'tcp   LISTEN 0 511   0.0.0.0:443  0.0.0.0:* users:(("nginx",pid=1,fd=6))\n'
        'tcp   LISTEN 0 244   127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=2,fd=5))\n'
        'tcp   LISTEN 0 4096  [::]:22  [::]:* users:(("sshd",pid=3,fd=4))\n'
        'tcp   LISTEN 0 5     172.17.0.1:15432 0.0.0.0:* users:(("socat",pid=4,fd=5))\n'
        "udp   UNCONN 0 0     0.0.0.0:68 0.0.0.0:*\n"
    )
    porte = {(p.porta, p.pubblica) for p in base.leggi_ss(ss)}
    assert porte == {(443, True), (5432, False), (22, True), (15432, False), (68, True)}
    proc = (
        "  sl  local_address rem_address   st\n"
        "   0: 00000000:1538 00000000:0000 0A\n"
        "   1: 0100007F:0016 00000000:0000 0A\n"
    )
    assert [(p.indirizzo, p.porta, p.pubblica) for p in base.leggi_proc_net(proc, "tcp", False)] == [
        ("0.0.0.0", 5432, True),
        ("127.0.0.1", 22, False),
    ]


def test_fine_supporto():
    oggi = dt.date(2026, 11, 27)
    assert base.fine_supporto("2025-05", oggi, "Ubuntu 20.04")[0] == base.ERRORE
    assert base.fine_supporto("2027-04", oggi, "x")[0] == base.ATTENZIONE
    assert base.fine_supporto("2029-05", oggi, "x")[0] == base.OK
    assert base.fine_supporto(None, oggi, "x") == (base.OK, "")


def test_esecuzione_mai_eccezioni():
    assert base.esegui(["comando-che-non-esiste-xyz"]).codice == 127
    assert base.esegui(["sleep", "5"], timeout=0.2).codice == 124


# --------------------------------------------------------------------------------------------- sistema
def test_sistema_letture():
    rel = sistema.leggi_os_release('PRETTY_NAME="Ubuntu 22.04.5 LTS"\nID=ubuntu\nVERSION_ID="22.04"\n')
    assert rel["ID"] == "ubuntu" and rel["VERSION_ID"] == "22.04"
    assert sistema.FINE_SUPPORTO["debian"]["11"] == "2026-08"
    mounts = (
        "/dev/sda1 / ext4 rw 0 0\nproc /proc proc rw 0 0\n"
        "/dev/sda1 /var/lib/docker ext4 rw 0 0\n/dev/sdb1 /srv xfs rw 0 0\n"
    )
    assert sistema.dischi(mounts) == [("/dev/sda1", "/"), ("/dev/sdb1", "/srv")]
    assert sistema.leggi_meminfo("MemTotal: 1024 kB\nMemAvailable: 512 kB\n") == {
        "MemTotal": 1048576,
        "MemAvailable": 524288,
    }


# --------------------------------------------------------------------------------------------- sicurezza
def test_sicurezza_firewall_e_porte_sensibili(monkeypatch):
    ufw = (
        "Status: active\n\nTo                         Action      From\n--  ------ ----\n"
        "80,443/tcp (Nginx Full)    ALLOW IN    Anywhere\n5432/tcp                   ALLOW IN    Anywhere\n"
        "22/tcp                     ALLOW IN    Anywhere\n15432/tcp ALLOW IN 172.18.0.0/16\n"
    )
    attivo, regole = sicurezza.leggi_ufw(ufw)
    assert attivo and sicurezza.porte_aperte_a_tutti(regole) == {5432, 22}

    def finto(cmd, timeout=30, **kw):
        if cmd[:2] == ["ufw", "status"]:
            return base.Uscita(0, ufw)
        return base.Uscita(127, "")

    monkeypatch.setattr(sicurezza, "esegui", finto)
    monkeypatch.setattr(sicurezza, "presente", lambda c: c == "ufw")
    monkeypatch.setattr(base.Contesto, "root", property(lambda self: True))
    ctx = _ctx()
    ctx._cache["porte"] = [
        base.Porta("tcp", "0.0.0.0", 5432, "postgres"),
        base.Porta("tcp", "0.0.0.0", 6379, "redis"),
        base.Porta("tcp", "0.0.0.0", 8888, "strano"),
    ]
    esiti = {e.nome: e for e in sicurezza.controlli(ctx)}
    assert esiti["Porta 5432 (PostgreSQL)"].esito == base.ERRORE  # aperta a Internet dal firewall
    assert esiti["Porta 6379 (Redis)"].esito == base.ATTENZIONE  # in ascolto ovunque ma bloccata
    assert "8888" in esiti["Altre porte esposte"].dettaglio


def test_sshd():
    c = sicurezza.leggi_sshd("permitrootlogin yes\npasswordauthentication no\nport 22\n")
    assert c["permitrootlogin"] == "yes" and c["passwordauthentication"] == "no"


# --------------------------------------------------------------------------------------------- web
def test_web_nomi_server_e_5xx():
    conf = (
        "server { listen 80; server_name a.esempio.it; return 301 https://$host$request_uri; }\n"
        "server { listen 443 ssl; server_name a.esempio.it www.a.esempio.it; }\n"
        "server { listen 443 ssl default_server; server_name _; }\n"
        "server { listen [::]:443 ssl http2; server_name b.esempio.it *.c.it localhost; }\n"
    )
    assert web.nomi_server(conf) == ["a.esempio.it", "www.a.esempio.it", "b.esempio.it"]
    adesso = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.timezone.utc)  # noqa: UP017
    righe = [
        '1.2.3.4 - - [27/Nov/2026:11:30:00 +0000] "GET / HTTP/1.1" 200 12 "-" "x"',
        '1.2.3.4 - - [27/Nov/2026:11:40:00 +0000] "GET /a HTTP/1.1" 502 12 "-" "x"',
        '1.2.3.4 - - [27/Nov/2026:09:00:00 +0000] "GET /b HTTP/1.1" 500 12 "-" "x"',  # più di un'ora fa
    ]
    assert web.errori_5xx(righe, adesso) == (2, 1)


# --------------------------------------------------------------------------------------------- posta
def test_posta_relay_coda_log():
    assert "tutta Internet" in posta.relay_aperto({"mynetworks": "127.0.0.0/8 0.0.0.0/0"})
    assert "reject_unauth_destination" in posta.relay_aperto({"smtpd_relay_restrictions": "permit_mynetworks, permit"})
    assert (
        posta.relay_aperto(
            {"mynetworks": "127.0.0.0/8", "smtpd_relay_restrictions": "permit_mynetworks, reject_unauth_destination"}
        )
        == ""
    )
    assert posta.relay_aperto({}) == ""  # predefinito di Postfix: sicuro
    coda = "\n".join(
        json.dumps(m)
        for m in ({"queue_name": "deferred", "arrival_time": 0}, {"queue_name": "active", "arrival_time": 3600})
    )
    assert posta.leggi_coda_postfix(coda, 7200) == (2, 1, 2.0)
    assert posta.leggi_mailq("-- 12 Kbytes in 3 Requests.") == 3
    oggi = dt.date(2026, 11, 27)
    log = [
        "Nov 27 10:00:00 mx postfix/smtp[1]: AB: to=<a@b.it>, status=sent (250 ok)",
        "Nov 26 10:00:00 mx postfix/smtp[1]: AC: to=<a@b.it>, status=deferred (timeout)",
        "Nov 27 10:01:00 mx postfix/smtpd[2]: warning: x[1.2.3.4]: SASL LOGIN authentication failed: x",
        "Nov 20 10:00:00 mx postfix/smtp[1]: AD: to=<a@b.it>, status=bounced (no)",  # vecchio
    ]
    assert posta.eventi_log(log, oggi) == {"rimbalzati": 0, "rinviati": 1, "accessi falliti": 1, "inviati": 1}


def test_posta_dns_dei_domini(monkeypatch):
    record = {
        ("esempio.it", "MX"): ["10 mx.esempio.it."],
        ("esempio.it", "TXT"): ["v=spf1 mx ~all", "google-site-verification=x"],
        ("_dmarc.esempio.it", "TXT"): ["v=DMARC1; p=quarantine"],
        ("default._domainkey.esempio.it", "TXT"): ["v=DKIM1; k=rsa; p=MII"],
        ("altro.it", "MX"): [],
        ("altro.it", "TXT"): ["v=spf1 +all", "v=spf1 mx -all"],
    }
    monkeypatch.setattr(posta, "dns", lambda nome, tipo: record.get((nome, tipo), []))
    monkeypatch.setattr(posta, "presente", lambda c: False)
    monkeypatch.setattr(posta, "primo_servizio", lambda *n: None)
    ctx = _ctx("[posta]\ndomini = esempio.it, altro.it\ndkim_selettori = default\n")
    ctx._cache["porte"] = []
    e = {x.nome: x for x in posta.controlli(ctx)}
    assert e["MX esempio.it"].esito == e["SPF esempio.it"].esito == e["DMARC esempio.it"].esito == base.OK
    assert e["DKIM esempio.it"].esito == base.OK and "quarantine" in e["DMARC esempio.it"].dettaglio
    assert e["MX altro.it"].esito == base.ERRORE and e["SPF altro.it"].esito == base.ERRORE  # due SPF
    assert e["DMARC altro.it"].esito == base.ATTENZIONE


# --------------------------------------------------------------------------------------------- database
def test_cluster_postgresql():
    testo = (
        "14  main 5432 online postgres /var/lib/postgresql/14/main /var/log/x.log\n16 prova 5433 down postgres /d /l\n"
    )
    assert database.leggi_cluster(testo) == [("14", "main", 5432, "online"), ("16", "prova", 5433, "down")]


def test_postgresql_controlli(monkeypatch):
    risposte = {
        "versione": "14.10",
        "ascolto": "*",
        "connessioni": "90/100",
        "lente": "1",
        "bloccate": "0",
        "eta_xid": "1200000000",
        "dimensioni": "metodo 2 GB",
        "trust": "1",
        "password_in_chiaro": "0",
    }
    sql = {v: k for k, v in database.SQL_PG.items()}
    monkeypatch.setattr(database, "_psql", lambda porta, q: risposte[sql[q]])
    monkeypatch.setattr(database, "presente", lambda c: True)
    monkeypatch.setattr(database, "esegui", lambda *a, **k: base.Uscita(0, "14 main 5432 online postgres /x /y\n"))
    monkeypatch.setattr(base.Contesto, "root", property(lambda self: True))
    e = {x.nome: x for x in database.postgresql(_ctx())}
    assert e["PostgreSQL 14/main: ascolto"].esito == base.ATTENZIONE
    assert e["PostgreSQL 14/main: connessioni"].esito == base.ATTENZIONE
    assert e["PostgreSQL 14/main: wraparound"].esito == base.ATTENZIONE
    assert e["PostgreSQL 14/main: pg_hba.conf"].esito == base.ERRORE


# --------------------------------------------------------------------------------------------- django
def test_django_letture(tmp_path):
    assert django.leggi_ambiente("# c\nexport DEBUG=False\nSECRET_KEY='abc=def'\nVUOTA=\n") == {
        "DEBUG": "False",
        "SECRET_KEY": "abc=def",
        "VUOTA": "",
    }
    uscita = (
        "System check identified some issues:\n\nWARNINGS:\n?: (security.W018) You should not have DEBUG set to True\n"
        "?: (security.W004) You have not set a value for SECURE_HSTS_SECONDS.\n\n"
        "System check identified 2 issues (0 silenced).\n"
    )
    assert django.leggi_check(uscita) == (["security.W018", "security.W004"], 2)
    assert django.FINE_DJANGO["4.2"] == "2026-04" and django.FINE_PYTHON["3.10"] == "2026-10"


def test_django_da_unita_systemd(tmp_path, monkeypatch):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "manage.py").write_text("")
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("")
    env = tmp_path / "app.env"
    env.write_text("DJANGO_SETTINGS_MODULE=progetto.settings\n")
    mostra = {
        "ExecStart": (
            f"{{ path={venv}/gunicorn ; argv[]={venv}/gunicorn --workers 3 --bind unix:/run/app.sock "
            "progetto.wsgi ; ignore_errors=no }"
        ),
        "WorkingDirectory": str(tmp_path / "app"),
        "User": "www-data",
        "Environment": "LANG=it_IT.UTF-8",
        "EnvironmentFiles": f"{env} (ignore_errors=no)",
    }
    monkeypatch.setattr(django, "systemctl_show", lambda unita, *p: mostra)
    a = django.da_unita("gunicorn_app.service")
    assert a.cartella == tmp_path / "app" and a.python == str(venv / "python") and a.utente == "www-data"
    assert a.socket == "/run/app.sock" and a.ambiente == {
        "LANG": "it_IT.UTF-8",
        "DJANGO_SETTINGS_MODULE": "progetto.settings",
    }
    mostra["ExecStart"] = "{ path=/usr/sbin/nginx ; argv[]=/usr/sbin/nginx -g daemon on; ; ignore_errors=no }"
    assert django.da_unita("nginx.service") is None


# --------------------------------------------------------------------------------------------- docker
def test_docker_container_e_spazio(monkeypatch):
    ps = "\n".join(
        json.dumps(c)
        for c in (
            {"Names": "web", "State": "running", "Status": "Up 2 hours (healthy)"},
            {"Names": "db", "State": "running", "Status": "Up 2 hours (unhealthy)"},
            {"Names": "worker", "State": "restarting", "Status": "Restarting (1) 5 seconds ago"},
            {"Names": "migrate", "State": "exited", "Status": "Exited (0) 1 hour ago"},
            {"Names": "vecchio", "State": "exited", "Status": "Exited (137) 2 days ago"},
        )
    )
    df = json.dumps({"Type": "Images", "Reclaimable": "12.5GB (60%)"})

    def finto(cmd, timeout=30, **kw):
        if cmd[1] == "info":
            return base.Uscita(0, "29.0.1\n")
        return base.Uscita(0, ps if cmd[1] == "ps" else df)

    monkeypatch.setattr(docker, "esegui", finto)
    e = {x.nome: x for x in docker.controlli(_ctx())}
    assert e["Container con problemi"].esito == base.ERRORE and "db (non sano)" in e["Container con problemi"].dettaglio
    assert e["Container fermati con errore"].dettaglio.startswith("vecchio")
    assert e["Spazio di Docker"].esito == base.ATTENZIONE


# --------------------------------------------------------------------------------------------- registro e comando
def test_registro_aree_scelte_e_area_rotta(monkeypatch):
    assert set(AREE) == {"sistema", "sicurezza", "web", "posta", "database", "django", "docker"}
    assert registro.rilevate(_ctx("[diagnostica]\naree = posta, web, inesistente\n")) == ["posta", "web"]

    def rotta(ctx):
        raise RuntimeError("guasto")

    monkeypatch.setitem(AREE, "web", registro.Area("web", "x", lambda c: True, rotta))
    (e,) = registro.esegui_aree(_ctx(), ["web"])
    assert e.esito == base.ERRORE and "guasto" in e.dettaglio


def test_comando_diagnostica_per_area_senza_metodo(tmp_path, monkeypatch, capsys):
    conf = mc.Conf(cartella=tmp_path / "manca", cartella_stato=tmp_path / "stato")
    monkeypatch.setattr(mc, "esegui_aree", lambda ctx, aree=None: [mc.Esito("Prova", mc.ATTENZIONE, "x", "posta")])
    a = mc.parser().parse_args(["diagnostica", "--area", "posta"])
    assert mc.cmd_diagnostica(conf, None, a) == 1
    assert "Posta" in capsys.readouterr().out
    assert mc.parser().parse_args(["diagnostica", "--elenco"]).elenco
    with pytest.raises(SystemExit):
        mc.parser().parse_args(["diagnostica", "--area", "inventata"])


def test_avviso_senza_metodo_con_sendmail(tmp_path, monkeypatch):
    conf = mc.Conf(cartella=tmp_path / "manca", avvisi_destinatari=["it@esempio.it"])
    inviati = []
    monkeypatch.setattr(mc, "invia_con_sendmail", lambda d, o, t: inviati.append((d, o)) or True)
    assert mc.invia_avviso(conf, None, "Problema", "testo")
    assert inviati == [(["it@esempio.it"], "Problema")]


def test_avvisi_solo_per_gli_errori(tmp_path, monkeypatch):
    conf = mc.Conf(cartella=tmp_path / "manca", cartella_stato=tmp_path / "stato", avvisi_livello=mc.ERRORE)
    inviati = []
    monkeypatch.setattr(mc, "invia_avviso", lambda c, d, oggetto, testo: inviati.append(oggetto) or True)
    esiti = [mc.Esito("Aggiornamenti", mc.ATTENZIONE, "3", "sistema")]
    monkeypatch.setattr(mc, "esegui_aree", lambda ctx, aree=None: esiti)
    a = mc.parser().parse_args(["diagnostica", "--avvisa", "--silenzioso"])
    assert mc.cmd_diagnostica(conf, None, a) == 1 and inviati == []  # solo un avviso: niente email
    esiti.append(mc.Esito("Porta 5432 (PostgreSQL)", mc.ERRORE, "aperta", "sicurezza"))
    assert mc.cmd_diagnostica(conf, None, a) == 2
    assert inviati == ["ERRORE: Porta 5432 (PostgreSQL)"]
