import json

import pytest
from django.core import mail
from django.core.management import call_command

from metodo.configurazione import servizi as config
from metodo.core import diagnostica


def test_salute_risponde_senza_login_e_con_qualunque_host(client, db):
    r = client.get("/salute/", HTTP_HOST="host-non-ammesso.example")
    dati = r.json()
    assert r.status_code == 200 and dati["stato"] == "ok" and dati["componenti"] == {"database": True, "cache": True}
    assert r["Cache-Control"] == "no-store"


def test_salute_503_se_un_componente_non_risponde(client, db, monkeypatch):
    monkeypatch.setattr("metodo.core.salute.controlla", lambda: {"database": False, "cache": True})
    r = client.get("/salute/")
    assert r.status_code == 503 and r.json()["stato"] == "errore"


def test_diagnostica_json(db, capsys):
    with pytest.raises(SystemExit) as uscita:
        call_command("diagnostica", "--json")
    dati = json.loads(capsys.readouterr().out)
    nomi = {c["nome"] for c in dati["controlli"]}
    assert {"Database", "Migrazioni", "Cache (Redis)", "Sicurezza"} <= nomi
    assert uscita.value.code == diagnostica.GRAVITA[dati["esito"]]
    assert next(c for c in dati["controlli"] if c["nome"] == "Migrazioni")["esito"] == "ok"


def test_un_controllo_che_si_rompe_diventa_un_errore(monkeypatch):
    def guasto():
        raise RuntimeError("boom")

    monkeypatch.setattr(diagnostica, "CONTROLLI", [guasto])
    (e,) = diagnostica.esegui()
    assert e.esito == "errore" and "boom" in e.dettaglio


def test_avviso_server(db, monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("Servizio worker fermo"))
    with pytest.raises(SystemExit) as uscita:
        call_command("avviso_server", "--oggetto", "ERRORE: worker")
    assert uscita.value.code == 3 and not mail.outbox  # nessun destinatario configurato
    config.scrivi("email.avvisi_server", "it@esempio.it")
    config.invalida_cache()
    monkeypatch.setattr("sys.stdin", io.StringIO("Servizio worker fermo"))
    call_command("avviso_server", "--oggetto", "ERRORE: worker")
    (m,) = mail.outbox
    assert m.to == ["it@esempio.it"] and "ERRORE: worker" in m.subject and "worker fermo" in m.body
