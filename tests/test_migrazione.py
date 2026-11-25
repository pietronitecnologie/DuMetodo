# ruff: noqa: E501  (dati SQL di prova)
import datetime as dt
import os
from decimal import Decimal
from io import StringIO

import psycopg
import pytest
from django.contrib.auth.models import User
from django.core.management import CommandError, call_command

from metodo.comunicazioni.models import Lato, LetturaNota, Nota
from metodo.consegne.models import DDT, RigaConsegnata
from metodo.core.models import Reparto
from metodo.ordini.models import Ordine, RigaOrdine, StatoOrdine, StatoRiga, StoricoScadenza

BASE = os.environ.get("DATABASE_URL", "postgres://metodo:metodo@localhost:5432/metodo").rsplit("/", 1)[0]
NOME_DB = "metodo_legacy_test"

SCHEMA = """
CREATE TABLE auth_user (id int PRIMARY KEY, username varchar, password varchar, first_name varchar, last_name varchar,
  email varchar, is_active bool, is_superuser bool, is_staff bool, date_joined timestamptz, last_login timestamptz);
CREATE TABLE auth_group (id int PRIMARY KEY, name varchar);
CREATE TABLE auth_user_groups (id serial, user_id int, group_id int);
CREATE TABLE "Plan_locpath" (id int PRIMARY KEY, code varchar);
CREATE TABLE "Plan_location" (id int PRIMARY KEY, area varchar, path_id int, site_id int);
CREATE TABLE "Prod_anagrafica" (id int PRIMARY KEY, articolo varchar, location_id int, um varchar, prezzo float,
  description text, tabulato varchar, prezzo_ricalcolato float, data_prezzo date, nuovo_articolo bool);
CREATE TABLE "Prod_ordine" (id int PRIMARY KEY, data_consegna date, data_inserimento timestamptz, data_scadenza date,
  stato varchar, urgenza varchar, tipo varchar, location_id int, rif_ordine varchar, anno varchar, serie varchar,
  appendice varchar);
CREATE TABLE "Prod_linea" (id int PRIMARY KEY, data_scadenza date, data_inserimento timestamptz, data_consegna date,
  rif_ordine varchar, anno varchar, appendice varchar, articolo varchar, qta float, prezzo_ea varchar,
  variante varchar, sconto_1 varchar, sconto_uni varchar, magazzino varchar, cliente varchar, serie varchar,
  n_riga varchar, evasione_parziale bool, emessa_bolla bool, blocca_linea bool, duplicata bool, annullata bool,
  scorporato bool);
CREATE TABLE "Prod_note" (id int PRIMARY KEY, user_id int, data timestamptz, testo text);
CREATE TABLE "Prod_ordine_note" (id serial, ordine_id int, note_id int);
CREATE TABLE "Prod_storicoscadenza" (id int PRIMARY KEY, ordine_id int, linea_id int, data_scadenza_precedente date,
  data_scadenza_nuova date, data_modifica timestamptz, utente_id int, motivo text);
CREATE TABLE "Prod_documentitrasporto" (id int PRIMARY KEY, no_ddt varchar, data_ddt date, data_inserimento date,
  rif_ordine varchar, anno varchar, appendice varchar, articolo varchar, qta float, variante varchar,
  cliente varchar, serie varchar, n_riga varchar, inviato bool);
"""

DATI = """
INSERT INTO auth_user VALUES (1,'mario','pbkdf2_sha256$1$x$y','Mario','Rossi','m@x.it',true,false,true,now(),null),
                             (2,'dmg','pbkdf2_sha256$1$x$z','','','d@x.it',true,false,false,now(),null);
INSERT INTO auth_group VALUES (1,'Geko'),(2,'Customer'),(3,'Altro');
INSERT INTO auth_user_groups (user_id, group_id) VALUES (1,1),(2,2),(2,3);
INSERT INTO "Plan_locpath" VALUES (1,'GK01-PLS');
INSERT INTO "Plan_location" VALUES (1,'Pulsanti',1,1);
INSERT INTO "Prod_anagrafica" VALUES (1,'ART-A',null,'EA',0.26,'',  '0',null,null,true),
  (2,'ART-A',1,'EA',0.26,'Pulsante rosso','2',null,null,false),(3,'ART-C',null,'M',null,null,'x',null,null,null);
-- ordine 4300 duplicato (appendice '0' e NULL sono la stessa chiave); 4301 con anno a 2 cifre
INSERT INTO "Prod_ordine" VALUES
  (10,null,'2024-03-01','2024-04-02','ANN','NRM','ASM',null,'0004300','2024','00040','0'),
  (11,null,'2024-03-05','2024-04-02','LAV','NRM','ASM',1,'0004300','2024','00040',null),
  (12,'2024-04-10','2024-03-06','2024-04-05','CON','ALT','ASM',null,'0004301','24','00040','T1'),
  (13,null,'2024-03-06',null,'Lav','NRM','XXX',null,'0004302','2024','00042','0');
INSERT INTO "Prod_linea" VALUES
  (100,'2024-04-02','2024-03-01',null,'0004300','2024','0','ART-A',2,'0,2596','__0',null,'',null,'00001636','00040','0000001',false,false,true,false,false,false),
  (101,'2024-04-02','2024-03-02',null,'0004300','2024','0','ART-A',3,'0,30','__0',null,'',null,'00001636','00040','0000001',false,false,false,false,false,false),
  (102,'2024-04-02','2024-03-01',null,'0004300','2024','0','ART-B',1,'abc',null,null,'',null,'00001636','00040','0000002',false,false,false,false,true,false),
  (103,'2024-04-05','2024-03-06','2024-04-10','0004301','24','T1','ART-C',4.5,'1,10',null,null,'',null,'','00040','0000001',true,true,false,false,false,true),
  (104,'2024-05-01','2024-03-07',null,'0009999','2024','0','ART-Z',1,'2',null,null,'',null,'','00040','0000001',false,false,false,false,false,false);
INSERT INTO "Prod_note" VALUES (1000,1,'2024-03-02 10:00+01','Nota su due ordini'),(1001,2,'2024-03-03 11:00+01','Domanda cliente');
INSERT INTO "Prod_ordine_note" (ordine_id, note_id) VALUES (11,1000),(12,1000),(10,1001);
INSERT INTO "Prod_storicoscadenza" VALUES (1,12,null,'2024-04-01','2024-04-05','2024-03-10',1,'ritardo fornitore');
INSERT INTO "Prod_documentitrasporto" VALUES
  (1,'77','2024-04-10','2024-04-10','0004301','24','T1','ART-C',4.5,null,'','00040','0000001',true),
  (2,null,null,'2024-04-10','0000000','2024','0','NONE',1,null,'','00040','0000001',false);
"""


@pytest.fixture
def metodo_db():
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {NOME_DB}")
        c.execute(f"CREATE DATABASE {NOME_DB}")
    url = f"{BASE}/{NOME_DB}"
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(SCHEMA)
        c.execute(DATI)
    yield url
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {NOME_DB} WITH (FORCE)")


def _esegui(url, *extra):
    out = StringIO()
    call_command("importa_da_metodo", "--db", url, *extra, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_prova_a_secco_non_salva_nulla(metodo_db):
    out = _esegui(metodo_db)
    assert "Prova a secco" in out and "ordini duplicati" in out
    assert not Ordine.objects.exists() and not User.objects.exists()


@pytest.mark.django_db
def test_migrazione_completa(metodo_db):
    out = _esegui(metodo_db, "--applica")
    assert "Migrazione completata" in out

    # utenti con password e ruoli
    mario, dmg = User.objects.get(username="mario"), User.objects.get(username="dmg")
    assert mario.password == "pbkdf2_sha256$1$x$y"
    from metodo.core.permessi import F, ha_permesso, is_cliente

    assert ha_permesso(mario, F.CONSEGNA) and not is_cliente(mario)  # Geko -> profilo Operatore
    assert is_cliente(dmg) and ha_permesso(dmg, F.ORDINI_VEDERE) and not ha_permesso(dmg, F.CONSEGNA)
    assert Reparto.objects.get(codice="GK01-PLS").nome == "Pulsanti"

    # chiave unica: il duplicato 4300 è fuso, vince il più recente non annullato
    o4300 = Ordine.objects.get(numero="0004300")
    assert (o4300.appendice, o4300.stato, o4300.reparto.codice, o4300.cliente) == (
        "",
        StatoOrdine.IN_LAVORAZIONE,
        "GK01-PLS",
        "00001636",
    )
    assert o4300.creato_il.date() == dt.date(2024, 3, 5)  # data originale conservata
    o4301 = Ordine.objects.get(numero="0004301")
    assert (o4301.anno, o4301.appendice, o4301.stato) == (2024, "T1", StatoOrdine.CONSEGNATO)
    assert Ordine.objects.get(numero="0004302").stato == StatoOrdine.IN_LAVORAZIONE  # 'Lav' di Metodo
    assert Ordine.objects.get(numero="0009999").stato == StatoOrdine.INSERITO  # creato dalla riga orfana

    # righe: prezzi decimali, righe doppie, annullate, flag
    righe = {r.pk: r for r in RigaOrdine.objects.filter(ordine=o4300)}
    attive = [r for r in righe.values() if r.stato == StatoRiga.ATTIVA]
    assert len(attive) == 1 and attive[0].qta == 3 and attive[0].prezzo_unitario == Decimal("0.3000")
    assert RigaOrdine.objects.get(ordine=o4300, qta=2).stato == StatoRiga.SOSTITUITA
    annullata = RigaOrdine.objects.get(articolo="ART-B")
    assert annullata.stato == StatoRiga.ANNULLATA and annullata.prezzo_unitario is None
    c = RigaOrdine.objects.get(articolo="ART-C")
    assert c.bolla_emessa and c.scorporata and not c.in_evasione and c.qta == Decimal("4.5")

    # note: una per ordine collegato, lato corretto, già lette
    assert Nota.objects.filter(testo="Nota su due ordini").count() == 2
    assert Nota.objects.get(testo="Domanda cliente").lato == Lato.CLIENTE
    assert Nota.objects.get(testo="Domanda cliente").ordine == o4300  # era sul duplicato id 10
    assert LetturaNota.objects.count() == Nota.objects.count() * 2

    s = StoricoScadenza.objects.get()
    assert (s.ordine, s.nuova, s.motivo, s.utente) == (o4301, dt.date(2024, 4, 5), "ritardo fornitore", mario)

    from metodo.anagrafica.models import Articolo

    a = Articolo.objects.get(codice="ART-A")
    assert (a.descrizione, a.reparto.codice, a.tabulato, a.nuovo) == ("Pulsante rosso", "GK01-PLS", 2, False)
    assert Articolo.objects.get(codice="ART-C").um == "M"

    # DDT
    rc = RigaConsegnata.objects.get()
    assert rc.riga == c and (rc.ddt.numero, rc.ddt.anno) == ("77", 2024)
    assert DDT.objects.count() == 1
    assert "riga d'ordine non trovata" in out and "prezzo 'abc' illeggibile" in out


@pytest.mark.django_db
def test_rifiuta_database_non_vuoto(metodo_db, ordine):
    with pytest.raises(CommandError, match="già degli ordini"):
        _esegui(metodo_db)


@pytest.mark.django_db
def test_schema_incompatibile(metodo_db):
    with psycopg.connect(metodo_db, autocommit=True) as c:
        c.execute('ALTER TABLE "Prod_linea" DROP COLUMN articolo')
    with pytest.raises(CommandError, match="articolo"):
        _esegui(metodo_db)
