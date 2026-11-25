"""disinstalla.sh su un finto sistema (METODOCONSOLE_RADICE), con un systemctl finto che registra le chiamate."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "MetodoConsole" / "disinstalla.sh"


@pytest.fixture
def sistema(tmp_path):
    r = tmp_path / "radice"
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    chiamate = tmp_path / "systemctl.log"
    (bin_ / "systemctl").write_text(f'#!/bin/sh\necho "$*" >> {chiamate}\n')
    (bin_ / "systemctl").chmod(0o755)
    for d in (
        "usr/local/bin",
        "opt/metodoconsole/venv/bin",
        "var/lib/metodoconsole",
        "etc/metodoconsole",
        "var/log/metodoconsole",
        "etc/systemd/system",
        "srv/backup-metodo/20261120-023000",
    ):
        (r / d).mkdir(parents=True)
    (r / "usr/local/bin/metodoconsole").symlink_to("/opt/repo/MetodoConsole/metodoconsole.py")
    (r / "usr/local/bin/altro").write_text("")
    for u in ("metodoconsole-backup.timer", "metodoconsole-backup.service", "altro.service"):
        (r / "etc/systemd/system" / u).write_text("")
    (r / "etc/metodoconsole/metodoconsole.conf").write_text(
        "[metodo]\ncartella = /opt/metodo\n[backup]\ncartella = /srv/backup-metodo\n"
    )
    (r / "srv/backup-metodo/20261120-023000/manifesto.json").write_text("{}")
    env = {**os.environ, "METODOCONSOLE_RADICE": str(r), "PATH": f"{bin_}:{os.environ['PATH']}"}
    return r, env, chiamate


def esegui(env, *argomenti, entrata=""):
    return subprocess.run(["sh", str(SCRIPT), *argomenti], env=env, input=entrata, capture_output=True, text=True)


def test_disinstallazione_normale_lascia_configurazione_e_backup(sistema):
    r, env, chiamate = sistema
    esito = esegui(env, "--si")
    assert esito.returncode == 0, esito.stderr
    assert not (r / "usr/local/bin/metodoconsole").is_symlink()
    assert (r / "usr/local/bin/altro").exists()  # gli altri comandi non si toccano
    assert not (r / "opt/metodoconsole").exists() and not (r / "var/lib/metodoconsole").exists()
    assert not (r / "etc/systemd/system/metodoconsole-backup.timer").exists()
    assert (r / "etc/systemd/system/altro.service").exists()
    assert (r / "etc/metodoconsole/metodoconsole.conf").exists() and (r / "var/log/metodoconsole").exists()
    assert (r / "srv/backup-metodo/20261120-023000").exists()
    log = chiamate.read_text()
    assert "disable --now metodoconsole-backup.timer" in log and "daemon-reload" in log
    assert "Metodo v2 continua a funzionare" in esito.stdout


def test_annullare_non_tocca_niente(sistema):
    r, env, _ = sistema
    esito = esegui(env, entrata="n\n")
    assert esito.returncode == 0 and "Annullato" in esito.stdout
    assert (r / "usr/local/bin/metodoconsole").is_symlink() and (r / "opt/metodoconsole").exists()


def test_configurazione_e_backup_solo_scrivendo_elimina(sistema):
    r, env, _ = sistema
    esito = esegui(env, "--si", "--configurazione", "--backup", entrata="no\n")
    assert esito.returncode == 0, esito.stderr
    assert not (r / "etc/metodoconsole").exists() and not (r / "var/log/metodoconsole").exists()
    assert "1 backup" in esito.stdout and "NON eliminati" in esito.stdout
    assert (r / "srv/backup-metodo/20261120-023000").exists()  # --si non basta per i backup
    (r / "etc/metodoconsole").mkdir()
    (r / "etc/metodoconsole/metodoconsole.conf").write_text("[backup]\ncartella = /srv/backup-metodo\n")
    esito = esegui(env, "--si", "--backup", entrata="ELIMINA\n")
    assert "Backup eliminati" in esito.stdout and not (r / "srv/backup-metodo").exists()


def test_rifiuta_cartelle_pericolose_e_comandi_estranei(sistema):
    r, env, _ = sistema
    (r / "etc/metodoconsole/metodoconsole.conf").write_text("[backup]\ncartella = /srv\n")
    (r / "usr/local/bin/metodoconsole").unlink()
    (r / "usr/local/bin/metodoconsole").symlink_to("/usr/bin/python3")
    esito = esegui(env, "--si", "--backup", entrata="ELIMINA\n")
    assert esito.returncode == 1 and "sospetta" in esito.stderr
    assert (r / "srv").exists() and (r / "usr/local/bin/metodoconsole").is_symlink()


def test_non_durante_un_backup(sistema, tmp_path):
    r, env, _ = sistema
    lucchetto = r / "var/lib/metodoconsole/operazione.lock"
    lucchetto.write_text("")
    with open(lucchetto) as f:
        import fcntl

        fcntl.flock(f, fcntl.LOCK_EX)
        esito = esegui(env, "--si")
    assert esito.returncode == 1 and "in corso" in esito.stderr
    assert (r / "opt/metodoconsole").exists()
