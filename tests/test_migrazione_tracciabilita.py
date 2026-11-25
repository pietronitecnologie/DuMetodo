# ruff: noqa: E501  (dati SQL di prova)
"""Migrazione da Metodo di tracciabilità e qualità (database di prova separato da test_migrazione)."""

import datetime as dt
import os
from decimal import Decimal
from io import StringIO

import psycopg
import pytest
from django.core.management import call_command
from django.utils import timezone

from metodo.ordini.models import Ordine
from metodo.tracciatura.models import Modello, Tracciatura

from .test_migrazione import DATI, SCHEMA

BASE = os.environ.get("DATABASE_URL", "postgres://metodo:metodo@localhost:5432/metodo").rsplit("/", 1)[0]
NOME_DB = "metodo_legacy_tracciabilita_test"

SCHEMA_TRACCIABILITA = """
CREATE TABLE "Prod_configuration" (id int PRIMARY KEY, chiave varchar, valore varchar);
CREATE TABLE "Plan_asset" (id int PRIMARY KEY, code varchar, name varchar, description varchar, location_id int);
CREATE TABLE "Plan_coding" (id int PRIMARY KEY, "internalCode" varchar, "rawMaterialCode" varchar);
CREATE TABLE "Prod_stored_item" (id int PRIMARY KEY, codice_id int, linea_id int, data_in timestamptz, data_out timestamptz);
CREATE TABLE "Prod_tracciabilita" (id int PRIMARY KEY, stored_item_id int, rif_ordine_id int, data_inserimento timestamptz,
  data timestamptz, chiave varchar, valore varchar, location_id int, asset_id int);
CREATE TABLE "Prod_check" (id int PRIMARY KEY, nome varchar, descrizione varchar);
CREATE TABLE "Prod_test" (id int PRIMARY KEY, nome varchar, descrizione varchar, tipo varchar, misura_min float,
  misura_max float, percentuale float, "checkDistinta" bool, "checkDisegno" bool);
CREATE TABLE "Prod_test_checks" (id serial PRIMARY KEY, test_id int, check_id int);
CREATE TABLE "Prod_controlloqlta" (id int PRIMARY KEY, ordine varchar, "codiceProdotto" varchar, check_rif_id int,
  data_inserimento timestamptz, campione varchar, ko varchar, "qtaInOrdine" varchar);
CREATE TABLE "Prod_reclami" (id int PRIMARY KEY, codice varchar, articolo varchar, ordine varchar, data timestamptz,
  reparto_id int, note text, file varchar);
CREATE TABLE "Prod_displaytest" (id int PRIMARY KEY, prodotto varchar, sito varchar, articolo varchar, esito varchar,
  fw varchar, lotto varchar, data date, note text, data_inserimento timestamptz, utente_id int);
CREATE TABLE "Prod_fileordini" (id int PRIMARY KEY, file varchar, rif_ordine varchar, anno varchar, serie varchar,
  appendice varchar, data_inserimento timestamptz);
"""

DATI_TRACCIABILITA = """
INSERT INTO "Prod_configuration" VALUES (1,'ITEM_CODE_CAVI','CVX');
INSERT INTO "Plan_asset" VALUES (1,'GKAST-107','Taglierina','',1),(2,'GKAST-107','Doppione','',1);
INSERT INTO "Plan_coding" VALUES (1,'BT00AA11',''),(2,'CVX123456','LOT-1'),(3,'GEN-1','');
INSERT INTO "Prod_stored_item" VALUES (1,1,null,null,null),(2,1,null,null,null),(3,2,null,null,null),(4,3,null,null,null);
INSERT INTO "Prod_tracciabilita" VALUES
  (1,1,11,'2024-03-01 10:00+01','2024-03-01 00:00+01','IN','',1,null),
  (2,2,null,'2024-05-01 10:00+01','2024-05-01 00:00+01','RC','',1,null),
  (3,1,null,'2024-05-02 10:00+01','2024-05-02 00:00+01','IN','',1,null),
  (4,3,11,'2024-03-02 10:00+01','2024-03-02 09:00+01','CV-AWG','1200',1,1),
  (5,3,11,'2024-03-02 10:01+01','2024-03-02 09:00+01','CV-AWG','1200',1,1),
  (6,null,11,'2024-03-03 10:00+01','2024-03-03 10:00+01','LOTTO','L-1',1,null),
  (7,4,null,'2024-03-04 10:00+01','2024-03-04 10:00+01','Collaudo','',null,null),
  (8,null,null,'2024-03-04 10:00+01',null,'X','',null,null);
INSERT INTO "Prod_check" VALUES (1,'Visivo',''),(8,'Distinta',''),(9,'Disegno','');
INSERT INTO "Prod_test" VALUES (1,'Pulsanti','', 'CMP',null,null,null,true,false);
INSERT INTO "Prod_test_checks" (test_id, check_id) VALUES (1,1);
INSERT INTO "Prod_controlloqlta" VALUES
  (1,'40.0004300','ART-A',1,'2024-03-10 00:00+01','20','2','100'),
  (2,'40.0004300','ART-A',8,'2024-03-10 00:00+01','0','Presente',null),
  (3,'STOCK','ART-B',1,'2024-03-11 00:00+01','5','9','5');
INSERT INTO "Prod_reclami" VALUES (1,'R-1','ART-A','40.0004300','2024-03-12 09:00+01',1,'graffi','Reclami/foto.jpg'),
  (2,'R-2','ART-X','99.999','2024-03-12 09:00+01',null,null,'');
INSERT INTO "Prod_displaytest" VALUES (1,'RAFFAELLO28','S1','A1','POS','1.0','L1','2024-03-13',null,'2024-03-13 08:00+01',1),
  (2,'RAFFAELLO28','S2','A2','BOH',null,null,'2024-03-13',null,'2024-03-13 08:00+01',1);
INSERT INTO "Prod_fileordini" VALUES (1,'file/foto1.jpg','0004300','2024','40','',now()),
  (2,'file/foto2.jpg','0000001','2024','40','',now());
"""


@pytest.fixture
def metodo_db():
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {NOME_DB}")
        c.execute(f"CREATE DATABASE {NOME_DB}")
    url = f"{BASE}/{NOME_DB}"
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(SCHEMA + SCHEMA_TRACCIABILITA)
        c.execute(DATI + DATI_TRACCIABILITA)
    yield url
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {NOME_DB} WITH (FORCE)")


@pytest.mark.django_db
def test_migrazione_tracciabilita_e_qualita(metodo_db):
    out = StringIO()
    call_command("importa_da_metodo", "--db", metodo_db, "--applica", stdout=out)
    out = out.getvalue()
    assert "Migrazione completata" in out
    o4300 = Ordine.objects.get(numero="0004300")

    # batterie, cavi e postazioni non vengono più importati: restano solo le tracce generiche
    # (diventano tracciature del modello «Tracce (da Metodo)»: evento, valore, reparto; codice = riferimento)
    tracce = {
        t.valori.get(campo__etichetta="Evento").testo: t
        for t in Tracciatura.objects.filter(modello__nome="Tracce (da Metodo)")
    }
    assert set(tracce) == {"LOTTO", "Collaudo"}
    assert tracce["LOTTO"].ordine == o4300 and tracce["LOTTO"].riferimento == ""
    assert tracce["Collaudo"].riferimento == "GEN-1" and tracce["Collaudo"].ordine is None
    assert "batterie e cavi (non importati)" in out

    # qualità: il test diventa un modello di tracciatura, le righe della stessa registrazione una tracciatura
    pulsanti = Modello.objects.get(nome="Pulsanti")
    assert pulsanti.categoria == "QUA"
    assert [c.etichetta for c in pulsanti.campi.order_by("ordine")] == [
        "Visivo · campione",
        "Visivo · KO",
        "Distinta",
        "Quantità in ordine",
    ]
    t = Tracciatura.objects.get(modello=pulsanti)
    assert (t.ordine, t.articolo, timezone.localtime(t.momento).date()) == (o4300, "ART-A", dt.date(2024, 3, 10))
    valori = {v.campo.etichetta: (v.intero, v.booleano, v.decimale) for v in t.valori.all()}
    assert valori["Visivo · campione"][0] == 20 and valori["Visivo · KO"][0] == 2 and valori["Distinta"][1] is True
    assert valori["Quantità in ordine"][2] == Decimal("100")
    assert t.esito == "KO"  # 2 pezzi KO
    assert "non validi" in out  # KO > campione

    # reclami e test display (presenti in Metodo) non vengono importati: funzioni non più usate
    [f] = Tracciatura.objects.filter(modello__nome="Foto e file dell'ordine")
    v = f.valori.get()
    assert (f.ordine, v.file.name, v.tipo_file, v.nome_file) == (o4300, "file/foto1.jpg", "image/jpeg", "foto1.jpg")
