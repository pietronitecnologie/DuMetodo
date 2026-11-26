# ruff: noqa: F811  (fixture importata)
"""Prelievo di lavoratori e timbrature dal Metodo precedente (app Timbratura, «arrivi in un luogo»)."""

import datetime as dt
from io import StringIO

import psycopg
import pytest
from django.core.management import call_command
from django.utils import timezone

from metodo.core.models import Reparto
from metodo.migrazione import timbrature as tb
from metodo.timbrature import servizi
from metodo.timbrature.models import Lavoratore, OrigineTimbratura, Timbratura

from .test_migrazione_tracciabilita import metodo_db  # noqa: F401  (fixture)

TIMBRATURE = """
INSERT INTO "Plan_locpath" VALUES (20,'GK01-IN'),(21,'GK01-OUT'),(22,'GK01-MSN'),(23,'GK01-ETT');
INSERT INTO "Plan_location" VALUES (20,'Ingresso',20,1),(21,'Uscita',21,1),(22,'Mensa',22,1),(23,'Elettronica',23,1);
CREATE TABLE "Plan_resource" (id int PRIMARY KEY, name varchar, lastname varchar, position varchar, code varchar,
  user_id int, active boolean, color varchar);
INSERT INTO "Plan_resource" VALUES (1,'Mario','Rossi','OP','R001',null,true,'blue'),
  (2,'Anna','Bianchi','OP','R002',null,false,'red');
CREATE TABLE "Timbratura_timbratura" (id int PRIMARY KEY, resource_id int, location_id int,
  date_created timestamptz, date timestamptz, duration interval);
-- Mario, lunedì: ingresso 8:00, pulsanti 8:05, mensa 12:00, elettronica 13:00, uscita 17:00
-- Anna, lunedì: parte direttamente in elettronica alle 9:00 (senza ingresso) ed esce alle 13:00
INSERT INTO "Timbratura_timbratura" VALUES
  (1,1,20,'2026-03-02 08:00+01','2026-03-02 08:00+01',null),
  (2,1,1, '2026-03-02 08:05+01','2026-03-02 08:05+01',null),
  (3,2,23,'2026-03-02 09:00+01','2026-03-02 09:00+01',null),
  (4,1,22,'2026-03-02 12:00+01','2026-03-02 12:00+01',null),
  (5,1,23,'2026-03-02 13:00+01','2026-03-02 13:00+01',null),
  (6,2,21,'2026-03-02 13:00+01','2026-03-02 13:00+01',null),
  (7,1,21,'2026-03-02 17:00+01','2026-03-02 17:00+01',null);
"""


@pytest.fixture
def con_timbrature(metodo_db):
    with psycopg.connect(metodo_db, autocommit=True) as c:
        c.execute(TIMBRATURE)
    return metodo_db


def _preleva(url, *extra):
    out = StringIO()
    call_command("preleva_da_metodo", "--db", url, "--pausa", "0", "--max-kb", "0", *extra, stdout=out)
    return out.getvalue()


def _eventi(lav):
    return [
        (timezone.localtime(t.momento).strftime("%H:%M"), t.tipo, t.reparto.codice if t.reparto else "")
        for t in Timbratura.objects.filter(lavoratore=lav).order_by("momento", "id")
    ]


@pytest.mark.django_db
def test_lavoratori_e_timbrature(con_timbrature):
    out = _preleva(con_timbrature, "--solo", "timbrature", "--blocco", "2")
    assert "timbrature: completato" in out
    mario, anna = Lavoratore.objects.get(matricola="R001"), Lavoratore.objects.get(matricola="R002")
    assert (mario.nome, mario.cognome, mario.attivo, anna.attivo) == ("Mario", "Rossi", True, False)
    assert _eventi(mario) == [
        ("08:00", "ENT", ""),
        ("08:05", "IRP", "GK01-PLS"),
        ("12:00", "IME", ""),
        ("13:00", "FME", ""),
        ("13:00", "IRP", "GK01-ETT"),
        ("17:00", "USC", ""),
    ]
    assert _eventi(anna) == [("09:00", "ENT", ""), ("09:00", "IRP", "GK01-ETT"), ("13:00", "USC", "")]
    assert set(Timbratura.objects.values_list("origine", flat=True)) == {OrigineTimbratura.METODO}
    # Le ore escono come in Metodo: 8:05-12 pulsanti, 13-17 elettronica, 1 ora di mensa
    pls, ett = Reparto.objects.get(codice="GK01-PLS"), Reparto.objects.get(codice="GK01-ETT")
    (turno,) = servizi.turni(dt.date(2026, 3, 2), dt.date(2026, 3, 2), [mario])[mario.pk]
    assert round(turno.per_reparto[pls.pk] / 60) == 235 and round(turno.per_reparto[ett.pk] / 60) == 240
    assert round(turno.mensa / 60) == 60 and round(turno.non_assegnato / 60) == 5 and not turno.anomalie


@pytest.mark.django_db
def test_incrementale_e_ripresa(con_timbrature):
    _preleva(con_timbrature, "--solo", "timbrature")
    prima = Timbratura.objects.count()
    assert "timbrature: riparto" in _preleva(con_timbrature, "--solo", "timbrature")
    assert Timbratura.objects.count() == prima  # niente doppioni
    # martedì Mario timbra ancora: il prelievo successivo porta solo la novità, ripartendo dallo stato di lunedì
    with psycopg.connect(con_timbrature, autocommit=True) as c:
        c.execute(
            """INSERT INTO "Timbratura_timbratura" VALUES (8,1,1,'2026-03-03 08:00+01','2026-03-03 08:00+01',null)"""
        )
    _preleva(con_timbrature, "--solo", "timbrature")
    mario = Lavoratore.objects.get(matricola="R001")
    assert _eventi(mario)[-2:] == [("08:00", "ENT", ""), ("08:00", "IRP", "GK01-PLS")]


def test_regole_di_conversione():
    t = dt.datetime(2026, 3, 2, 8, tzinfo=dt.UTC)
    ora = lambda h: t + dt.timedelta(hours=h)  # noqa: E731
    assert (
        tb.luogo("gk01-in") == tb.INGRESSO and tb.luogo("X", "Mensa") == tb.MENSA and tb.luogo("GK01-PLS") == tb.REPARTO
    )
    # dentro in reparto, torna all'ingresso: fine reparto
    eventi, s = tb.converti(tb.Stato(tb.REPARTO, 5, t), tb.INGRESSO, None, ora(1))
    assert eventi == [("FRP", None)] and s.dove == tb.INGRESSO
    # dalla mensa all'ingresso: fine mensa e fine del reparto sospeso
    eventi, _ = tb.converti(tb.Stato(tb.MENSA, None, t), tb.INGRESSO, None, ora(1))
    assert eventi == [("FME", None), ("FRP", None)]
    # stesso reparto timbrato due volte: niente evento doppio
    assert tb.converti(tb.Stato(tb.REPARTO, 5, t), tb.REPARTO, 5, ora(1))[0] == []
    # più di 16 ore dopo: nuovo turno con entrata
    assert tb.converti(tb.Stato(tb.REPARTO, 5, t), tb.REPARTO, 5, ora(20))[0] == [("ENT", None), ("IRP", 5)]
    # uscita senza entrata: registrata com'è
    assert tb.converti(tb.Stato(), tb.USCITA, None, t)[0] == [("USC", None)]
    assert tb.stato_da_evento("FME", None, t).dove == tb.INGRESSO
