"""MetodoConsole: le parti che non richiedono Docker (rotazione, avvisi, backup su disco, configurazione)."""

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "metodoconsole", Path(__file__).resolve().parent.parent / "MetodoConsole" / "metodoconsole.py"
)
mc = importlib.util.module_from_spec(_spec)
sys.modules["metodoconsole"] = mc  # serve alle dataclass del modulo
_spec.loader.exec_module(mc)


def _b(quando: str, nota: str = "") -> "mc.Backup":
    d = dt.datetime.strptime(quando, "%Y-%m-%d %H:%M")
    nome = d.strftime(mc.FORMATO_NOME) + (f"-{nota}" if nota else "")
    return mc.Backup(nome, Path(nome), d, 1, {})


def test_rotazione_giorni_settimane_mesi():
    # un backup al giorno per 120 giorni, dal più recente
    oggi = dt.datetime(2026, 11, 20, 2, 30)
    elenco = [_b((oggi - dt.timedelta(days=i)).strftime("%Y-%m-%d %H:%M")) for i in range(120)]
    via = {b.nome for b in mc.da_eliminare(elenco, 7, 4, 3)}
    tenuti = [b for b in elenco if b.nome not in via]
    assert elenco[0] in tenuti and all(b in tenuti for b in elenco[:7])  # ultimi 7 giorni
    assert len(tenuti) <= 7 + 4 + 3 and elenco[-1].nome in via
    mesi = {(b.quando.year, b.quando.month) for b in tenuti}
    assert len(mesi) == 3


def test_rotazione_due_backup_nello_stesso_giorno_e_ultimo_mai_eliminato():
    elenco = [_b("2026-11-20 10:00", "prima-di-abc"), _b("2026-11-20 02:30"), _b("2026-11-19 02:30")]
    via = mc.da_eliminare(elenco, 1, 0, 0)
    assert [b.nome for b in via] == [elenco[1].nome, elenco[2].nome]
    assert mc.da_eliminare(elenco[:1], 0, 0, 0) == []


@pytest.mark.parametrize(
    ("precedente", "esito", "firma", "trascorse_ore", "atteso"),
    [
        ({}, mc.OK, "", 0, None),
        ({}, mc.ERRORE, "web:errore", 0, "problema"),
        ({"esito": mc.ERRORE, "firma": "web:errore", "avvisato_il": 0}, mc.ERRORE, "web:errore", 1, None),
        ({"esito": mc.ERRORE, "firma": "web:errore", "avvisato_il": 0}, mc.ERRORE, "web:errore", 7, "problema"),
        ({"esito": mc.ERRORE, "firma": "web:errore", "avvisato_il": 0}, mc.ERRORE, "db:errore", 1, "problema"),
        ({"esito": mc.ATTENZIONE, "firma": "x", "avvisato_il": 0}, mc.OK, "", 1, "risolto"),
    ],
)
def test_quando_avvisare(precedente, esito, firma, trascorse_ore, atteso):
    adesso = precedente.get("avvisato_il", 0) + trascorse_ore * 3600
    assert mc.decidi_avviso(precedente, esito, firma, adesso, 6) == atteso


def _crea_backup(cartella: Path, nome: str, contenuto: bytes = b"PGDMP...") -> Path:
    p = cartella / nome
    p.mkdir(parents=True)
    (p / "database.dump").write_bytes(contenuto)
    manifesto = {"file": {"database.dump": {"sha256": mc.impronta(p / "database.dump"), "byte": len(contenuto)}}}
    (p / "manifesto.json").write_text(json.dumps(manifesto))
    return p


def test_elenco_ricerca_e_verifica(tmp_path):
    conf = mc.Conf(backup_cartella=tmp_path)
    _crea_backup(tmp_path, "20261119-023000")
    p = _crea_backup(tmp_path, "20261120-023000-prima-di-abc1234")
    (tmp_path / ".20261120-030000.incompleto").mkdir()  # backup interrotto: ignorato
    assert [b.nome for b in mc.elenca_backup(conf)] == ["20261120-023000-prima-di-abc1234", "20261119-023000"]
    assert mc.trova_backup(conf, "ultimo").percorso == p
    assert mc.trova_backup(conf, "20261119").nome == "20261119-023000"
    assert mc.trova_backup(conf, "abc1234").percorso == p  # parte della nota
    with pytest.raises(mc.Errore, match="indica 2 backup"):
        mc.trova_backup(conf, "2026")
    b = mc.trova_backup(conf, "ultimo")
    assert mc.verifica_backup(conf, None, b) == []
    (p / "database.dump").write_bytes(b"alterato")
    assert "alterato" in mc.verifica_backup(conf, None, b)[0]


def test_configurazione(tmp_path):
    f = tmp_path / "mc.conf"
    f.write_text(
        "[metodo]\ncartella = /srv/dm\nfile_compose = compose.yaml compose.prod.yaml\n"
        "[backup]\nconserva_giornalieri = 3\ncopia_remota = nas:/backup\n[avvisi]\nemail = no\n"
    )
    c = mc.leggi_conf(str(f))
    assert c.cartella == Path("/srv/dm") and c.file_compose == ["compose.yaml", "compose.prod.yaml"]
    assert c.conserva_giornalieri == 3 and c.conserva_settimanali == 8 and c.copia_remota == "nas:/backup"
    assert c.avvisi_email is False
    base = mc.Compose(c).base()
    assert base[:4] == ["docker", "compose", "--project-directory", "/srv/dm"] and "/srv/dm/compose.prod.yaml" in base
    with pytest.raises(mc.Errore):
        mc.leggi_conf(str(tmp_path / "manca.conf"))


def test_riga_di_comando():
    a = mc.parser().parse_args(["ripristina", "ultimo", "--solo-database", "--si"])
    assert a.funzione is mc.cmd_ripristina and a.solo_database and a.si
    assert mc.parser().parse_args(["avvia", "--ricostruisci"]).ricostruisci


# ------------------------------------------------------------------------------------- prelievo da Metodo
class _ComposeFinto:
    def __init__(self):
        self.chiamate = []

    def __call__(self, *argomenti, **kw):
        self.chiamate.append((argomenti, kw))
        return mc.subprocess.CompletedProcess(argomenti, 0, "", "")

    def servizi(self):
        return {"web": {"State": "running"}, "db": {"State": "running"}}


def _conf(tmp_path, env=""):
    (tmp_path / ".env").write_text(env)
    return mc.Conf(cartella=tmp_path, cartella_stato=tmp_path / "stato", progetto="metodo")


def test_prelievo_avvia_con_backup_e_opzioni(tmp_path, monkeypatch):
    conf = _conf(tmp_path, "METODO_DB_URL='postgres://u:p@metodo/db'\n")
    dc = _ComposeFinto()
    backup = []
    monkeypatch.setattr(mc, "stato_container", lambda nome: None)
    monkeypatch.setattr(
        mc, "esegui_backup", lambda c, d, nota, con_file: backup.append(nota) or mc.Backup("b", tmp_path, None, 0, {})
    )
    a = mc.parser().parse_args(["prelievo", "avvia", "--max-kb", "256", "--pausa", "1", "--ricomincia", "ordini"])
    assert mc.cmd_prelievo(conf, dc, a) == 0
    assert backup == ["prima-del-prelievo"]
    ((argomenti, kw),) = dc.chiamate
    assert argomenti[:4] == ("run", "-d", "--name", "metodo-prelievo") and "-e" not in argomenti
    assert argomenti[argomenti.index("--max-kb") + 1] == "256.0" and "--ricomincia" in argomenti
    assert "--log" in argomenti and kw["ambiente"] == {}  # URL già nel .env: non passa dalla riga di comando


def test_prelievo_non_parte_due_volte_e_url_mancante(tmp_path, monkeypatch):
    conf = _conf(tmp_path)
    monkeypatch.setattr(mc, "stato_container", lambda nome: {"Running": True})
    a = mc.parser().parse_args(["prelievo", "avvia"])
    with pytest.raises(mc.Errore, match="già in corso"):
        mc.cmd_prelievo(conf, _ComposeFinto(), a)
    monkeypatch.delenv("METODO_DB_URL", raising=False)
    monkeypatch.setattr(mc.sys.stdin, "isatty", lambda: False)
    with pytest.raises(mc.Errore, match="METODO_DB_URL"):
        mc.url_metodo(conf)
    monkeypatch.setenv("METODO_DB_URL", "postgres://x")
    assert mc.url_metodo(conf) == {"METODO_DB_URL": "postgres://x"}


def test_prelievo_nella_diagnostica():
    assert mc.descrivi_prelievo(None) is None
    assert mc.descrivi_prelievo({"Running": True, "StartedAt": "2026-11-22T20:00:00.1Z"}).esito == mc.OK
    e = mc.descrivi_prelievo({"Running": False, "ExitCode": 1, "FinishedAt": "2026-11-22T21:00:00Z"})
    assert e.esito == mc.ATTENZIONE and "prelievo avvia" in e.dettaglio
    assert mc.descrivi_prelievo({"Running": False, "ExitCode": 0, "FinishedAt": "x"}).dettaglio == "completato il x"


# =========================================================================== pannello (curses)
_spec_p = importlib.util.spec_from_file_location(
    "pannello", Path(__file__).resolve().parent.parent / "MetodoConsole" / "pannello.py"
)
pan = importlib.util.module_from_spec(_spec_p)
sys.modules["pannello"] = pan
_spec_p.loader.exec_module(pan)

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


def test_pannello_legge_e_ordina_i_container():
    elenco = pan.ordina(pan.leggi_container(PS), "metodo")
    assert [c.nome for c in elenco] == ["metodo-db-1", "metodo-migrate-1", "metodo-web-1", "dumetodo-web-1"]
    db, migrate, web, vecchio = elenco
    assert (pan.stato(db), pan.stato(migrate), pan.stato(web), pan.stato(vecchio)) == (
        pan.AVVIO,
        pan.ERRORE,
        pan.OK,
        pan.OK,
    )
    assert migrate.codice_uscita == 1 and web.salute == "healthy" and db.salute == "starting"
    assert pan.porte(web) == "8000→8000"
    testo = pan.riepilogo(elenco, "metodo")
    assert "1/3 attivi" in testo and "errori (migrate" in testo
    assert "altri container accesi: 1 (dumetodo-web-1 su 8000→8000)" in testo
    # Anche il formato a lista di alcune versioni di docker.
    assert len(pan.leggi_container("[" + ",".join(PS.splitlines()) + "]")) == 4


def test_pannello_statistiche_e_filtri_dei_log():
    elenco = pan.leggi_container(PS)
    pan.unisci_statistiche(elenco, '{"Name": "metodo-web-1", "CPUPerc": "3.2%", "MemUsage": "180MiB / 7.6GiB"}\nrotto')
    assert (elenco[0].cpu, elenco[0].memoria) == ("3.2%", "180MiB")
    righe = ["avvio", "Traceback (most recent call last):", "ok", "django ERROR qualcosa"]
    assert pan.righe_di_errore(righe) == [righe[1], righe[3]]
    assert pan.filtra(righe, "AVV") == ["avvio"] and pan.filtra(righe, "") == righe


def test_pannello_in_un_terminale(tmp_path):
    """Il pannello vero, in un terminale finto, con un «docker» finto: si apre, mostra l'aiuto, si chiude."""
    import os
    import pty
    import select
    import subprocess
    import time

    docker = tmp_path / "docker"
    ps = tmp_path / "ps.json"
    ps.write_text(PS)
    docker.write_text(
        "#!/bin/sh\n"
        f'case "$*" in ps*) cat {ps} ;; *config*) echo \'{{"name": "metodo"}}\' ;; logs*) echo "riga di log" ;; esac\n'
    )
    docker.chmod(0o755)
    conf = tmp_path / "mc.conf"
    conf.write_text(f"[metodo]\ncartella = {tmp_path}\n[backup]\ncartella = {tmp_path / 'backup'}\n")
    script = Path(__file__).resolve().parent.parent / "MetodoConsole" / "metodoconsole.py"
    madre, figlia = pty.openpty()
    os.set_blocking(madre, False)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "TERM": "xterm", "LANG": "C.UTF-8"}
    p = subprocess.Popen(
        [sys.executable, str(script), "--conf", str(conf), "pannello"],
        stdin=figlia,
        stdout=figlia,
        stderr=figlia,
        env=env,
        close_fds=True,
    )
    os.close(figlia)
    schermo = b""

    def leggi(secondi: float) -> None:
        nonlocal schermo
        fine = time.time() + secondi
        while time.time() < fine:
            if select.select([madre], [], [], 0.1)[0]:
                with contextlib.suppress(OSError):
                    schermo += os.read(madre, 65536)

    import contextlib

    leggi(3)
    for tasto in (b"j", b"\r"):  # scendi sul secondo container e apri il suo log
        os.write(madre, tasto)
        leggi(1)
    os.write(madre, b"q")
    leggi(1)
    os.write(madre, b"?")
    leggi(1)
    os.write(madre, b"q")
    leggi(1)
    os.write(madre, b"q")
    leggi(2)
    assert p.wait(timeout=10) == 0
    testo = schermo.decode(errors="replace")
    assert "MetodoConsole" in testo and "migrate" in testo and "dumetodo-web-1" in testo
    assert "riga di log" in testo and "Pannello di MetodoConsole" in testo
