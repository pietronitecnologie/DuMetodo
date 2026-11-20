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
        "[dumetodo]\ncartella = /srv/dm\nfile_compose = compose.yaml compose.prod.yaml\n"
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
