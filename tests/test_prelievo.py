# ruff: noqa: F811, E501  (fixture importata; dati SQL di prova)
"""Prelievo a blocchi da Metodo: stesso risultato dell'importatore, ma riprendibile e incrementale."""

from decimal import Decimal
from io import StringIO

import psycopg
import pytest
from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.db import transaction

from metodo.anagrafica.models import Articolo
from metodo.comunicazioni.models import LetturaNota, Nota
from metodo.consegne.models import DDT, RigaConsegnata
from metodo.core.models import Reparto
from metodo.migrazione import prelievo as modulo
from metodo.migrazione.models import Avanzamento
from metodo.migrazione.prelievo import Limitatore
from metodo.ordini.models import Ordine, RigaOrdine, StatoOrdine, StoricoScadenza
from metodo.tracciatura.models import Tracciatura, Valore

from .test_migrazione_tracciabilita import metodo_db  # noqa: F401  (fixture)


def _k(o):
    return (o.serie, o.numero, o.appendice, o.anno)


def _fotografia():
    """Tutto ciò che la migrazione produce, in forma confrontabile (senza le chiavi primarie)."""
    return {
        "utenti": sorted(  # la password dell'utente di sistema è casuale (e inutilizzabile)
            (u, "!" if p.startswith("!") else p, a)
            for u, p, a in User.objects.values_list("username", "password", "is_active")
        ),
        "reparti": sorted(Reparto.objects.values_list("codice", "nome")),
        "articoli": sorted(Articolo.objects.values_list("codice", "descrizione", "tabulato", "nuovo")),
        "ordini": sorted(
            (
                _k(o),
                o.stato,
                o.urgenza,
                o.tipo,
                o.reparto_id and o.reparto.codice,
                o.cliente,
                o.data_scadenza,
                o.data_consegna,
                o.creato_il,
            )
            for o in Ordine.objects.all()
        ),
        "righe": sorted(
            (
                _k(r.ordine),
                r.n_riga,
                r.articolo,
                r.variante,
                r.qta,
                r.prezzo_unitario,
                r.stato,
                r.bloccata,
                r.in_evasione,
                r.bolla_emessa,
                r.scorporata,
                r.creato_il,
            )
            for r in RigaOrdine.objects.select_related("ordine")
        ),
        "note": sorted((_k(n.ordine), n.testo, n.lato, n.creata_il) for n in Nota.objects.select_related("ordine")),
        "letture": LetturaNota.objects.count(),
        "storico": sorted(
            (_k(s.ordine), s.precedente, s.nuova, s.motivo, s.utente.username if s.utente else None)
            for s in StoricoScadenza.objects.all()
        ),
        "consegnate": sorted(
            (_k(rc.riga.ordine), rc.riga.n_riga, rc.qta, rc.ddt and (rc.ddt.numero, rc.ddt.anno))
            for rc in RigaConsegnata.objects.all()
        ),
        "ddt": sorted(DDT.objects.values_list("numero", "anno", "data")),
        "tracciature": sorted(
            (
                (t.modello.nome, t.ordine and _k(t.ordine), t.articolo, t.riferimento, t.esito, t.momento)
                for t in Tracciatura.objects.select_related("modello", "ordine")
            ),
            key=repr,
        ),
        "valori": sorted(
            (
                (v.campo.etichetta, v.intero, v.booleano, v.decimale, v.file.name, v.testo)
                for v in Valore.objects.select_related("campo")
            ),
            key=repr,
        ),
    }


def _preleva(url, *extra):
    out = StringIO()
    call_command("preleva_da_metodo", "--db", url, "--pausa", "0", "--max-kb", "0", *extra, stdout=out)
    return out.getvalue()


def _con_importatore(url):
    with transaction.atomic():
        call_command("importa_da_metodo", "--db", url, "--applica", stdout=StringIO())
        foto = _fotografia()
        transaction.set_rollback(True)
    return foto


@pytest.mark.django_db
@pytest.mark.parametrize("blocco", ["1", "3", "2000"])
def test_stesso_risultato_dell_importatore(metodo_db, blocco):
    atteso = _con_importatore(metodo_db)
    assert atteso["ordini"] and atteso["righe"] and atteso["tracciature"]
    out = _preleva(metodo_db, "--blocco", blocco)
    assert "Prelievo completato" in out and "ordini duplicati" in out
    assert _fotografia() == atteso


@pytest.mark.django_db
def test_rilanciato_non_crea_doppioni(metodo_db):
    _preleva(metodo_db, "--blocco", "2")
    prima = _fotografia()
    out = _preleva(metodo_db, "--blocco", "2")
    assert "magazzino: già importato" in out and "qualita: già importato" in out
    assert _fotografia() == prima


@pytest.mark.django_db
def test_interrotto_e_ripreso(metodo_db, monkeypatch):
    atteso = _con_importatore(metodo_db)
    originale = modulo.Prelievo._blocco_righe
    chiamate = {"n": 0}

    def guasto(self, blocco):
        chiamate["n"] += 1
        if chiamate["n"] == 2:
            originale(self, blocco)  # lavoro fatto e poi perso: la transazione del blocco viene annullata
            raise KeyboardInterrupt
        originale(self, blocco)

    monkeypatch.setattr(modulo.Prelievo, "_blocco_righe", guasto)
    with pytest.raises(SystemExit):
        _preleva(metodo_db, "--blocco", "2")
    righe = Avanzamento.objects.get(passo="righe")
    assert righe.elaborati == 2 and righe.completato_il is None
    assert not Avanzamento.objects.filter(passo="note").exists()  # i passi successivi non sono partiti

    monkeypatch.setattr(modulo.Prelievo, "_blocco_righe", originale)
    out = _preleva(metodo_db, "--blocco", "2")
    assert f"righe: riparto dall'id {righe.ultimo_id}" in out
    assert _fotografia() == atteso


@pytest.mark.django_db
def test_riconnessione_se_metodo_non_risponde(metodo_db, monkeypatch):
    originale = modulo.SorgenteRemota._connetti
    guasti = {"n": 0}

    def a_singhiozzo(self):
        guasti["n"] += 1
        if guasti["n"] in (1, 4):
            raise psycopg.OperationalError("connessione chiusa dal server")
        return originale(self)

    attese = []
    monkeypatch.setattr(modulo.SorgenteRemota, "_connetti", a_singhiozzo)
    monkeypatch.setattr(modulo.time, "sleep", attese.append)
    monkeypatch.setattr(modulo.SorgenteRemota, "esegui", _chiude_ogni_tanto(modulo.SorgenteRemota.esegui))
    out = _preleva(metodo_db, "--blocco", "2")
    assert "Prelievo completato" in out and "nuovo tentativo tra 2 s" in out
    assert 2.0 in attese
    assert Ordine.objects.count() == 4


def _chiude_ogni_tanto(esegui):
    """Fa cadere la connessione ogni 5 query, per provare la riconnessione a metà prelievo."""
    contatore = {"n": 0}

    def avvolto(self, *a, **k):
        contatore["n"] += 1
        if contatore["n"] % 5 == 0 and self.conn is not None:
            self.conn.close()
        return esegui(self, *a, **k)

    return avvolto


@pytest.mark.django_db
def test_si_arrende_dopo_i_tentativi(metodo_db, monkeypatch):
    monkeypatch.setattr(modulo.time, "sleep", lambda _s: None)

    def sempre_giu(self):
        raise psycopg.OperationalError("rete giù")

    monkeypatch.setattr(modulo.SorgenteRemota, "_connetti", sempre_giu)
    with pytest.raises(CommandError, match="Rilancia il comando per riprendere"):
        _preleva(metodo_db, "--tentativi", "3")


@pytest.mark.django_db
def test_incrementale_e_ricomincia(metodo_db):
    _preleva(metodo_db, "--blocco", "2")
    with psycopg.connect(metodo_db, autocommit=True) as c:
        c.execute(
            """INSERT INTO "Prod_ordine" VALUES (14,null,'2024-06-01',null,'INS','NRM','ASM',null,'0004400','2024','00040','0');
               INSERT INTO "Prod_linea" VALUES (105,'2024-07-01','2024-06-01',null,'0004400','2024','0','ART-N',7,'1,5',
                 null,null,'',null,'00009','00040','0000001',false,false,false,false,false,false);
               UPDATE "Prod_ordine" SET stato='CON' WHERE id=13;"""
        )
    out = _preleva(metodo_db, "--blocco", "2")
    nuovo = Ordine.objects.get(numero="0004400")
    assert nuovo.cliente == "00009" and nuovo.righe.get().qta == 7
    assert "ordini: 1" in out and "righe: 1" in out
    o4302 = Ordine.objects.get(numero="0004302")
    # Era aperto in Metodo v2: l'allineamento lo rilegge anche senza --ricomincia.
    assert o4302.stato == StatoOrdine.CONSEGNATO and "allinea:" in out

    out = _preleva(metodo_db, "--blocco", "2", "--ricomincia", "ordini", "righe")
    o4302.refresh_from_db()
    assert o4302.stato == StatoOrdine.CONSEGNATO
    assert Ordine.objects.count() == 5 and RigaOrdine.objects.count() == 6
    assert "ordini aggiornati" in out


@pytest.mark.django_db
def test_allineamento_porta_i_cambi_degli_ordini_vivi(metodo_db):
    """Coesistenza: rilanciato, il prelievo porta in Metodo v2 i cambi fatti in Metodo sugli ordini vivi."""
    _preleva(metodo_db)
    riga_102 = RigaOrdine.objects.get(ordine__numero="0004300", articolo="ART-B")
    assert not riga_102.bolla_emessa
    with psycopg.connect(metodo_db, autocommit=True) as c:
        c.execute(
            """UPDATE "Prod_linea" SET emessa_bolla = true, data_consegna = current_date WHERE id = 102;
               UPDATE "Prod_ordine" SET stato = 'CON', data_consegna = current_date WHERE id = 11;
               UPDATE "Prod_ordine" SET stato = 'CON' WHERE id = 13;
               UPDATE "Prod_linea" SET qta = 9 WHERE id = 103;
               INSERT INTO "Prod_linea" VALUES (106,'2024-04-02',now(),null,'0004300','2024','0','ART-N',5,'1',
                 null,null,'',null,'00001636','00040','0000009',false,false,false,false,false,false);"""
        )
    out = _preleva(metodo_db)
    riga_102.refresh_from_db()
    assert riga_102.bolla_emessa  # riga di un ordine aperto: aggiornata
    assert Ordine.objects.get(numero="0004300", stato=StatoOrdine.CONSEGNATO)  # aperto in Metodo v2: aggiornato
    assert Ordine.objects.get(numero="0004302").stato == StatoOrdine.CONSEGNATO  # chiuso in Metodo da tempo
    assert RigaOrdine.objects.get(ordine__numero="0004300", articolo="ART-N").qta == 5  # riga nuova
    riga_103 = RigaOrdine.objects.get(ordine__numero="0004301")
    assert riga_103.qta == Decimal("4.5")  # ordine chiuso da anni, fuori dalla finestra: non riletto
    assert "allineamento: cambi di stato: 2" in out and "allineamento: righe modificate" in out
    assert RigaOrdine.objects.count() == 6 and Ordine.objects.count() == 4  # nessun doppione

    out = _preleva(metodo_db, "--giorni", "5000")  # finestra larga: anche gli ordini vecchi
    riga_103.refresh_from_db()
    assert riga_103.qta == 9
    out = _preleva(metodo_db)  # niente di nuovo: niente da modificare
    assert "0 modificati (0 cambi di stato), 0 righe modificate, 0 righe nuove" in out
    assert RigaOrdine.objects.count() == 6


@pytest.mark.django_db
def test_ordine_gia_presente_in_metodo_non_viene_toccato(metodo_db, ordine):
    ordine.serie, ordine.numero, ordine.appendice, ordine.anno = "00042", "0004302", "", 2024
    ordine.stato = StatoOrdine.CONSEGNATO
    ordine.save()
    out = _preleva(metodo_db)
    ordine.refresh_from_db()
    assert ordine.stato == StatoOrdine.CONSEGNATO
    assert "già presenti in Metodo v2" in out


@pytest.mark.django_db
def test_verifica_e_stato(metodo_db):
    out = _preleva(metodo_db, "--verifica")
    assert "Connessione a Metodo riuscita" in out and "Prod_linea" in out
    assert not Ordine.objects.exists()
    out = StringIO()
    call_command("preleva_da_metodo", "--stato", stdout=out)
    assert "Nessun prelievo" in out.getvalue()
    _preleva(metodo_db)
    out = StringIO()
    call_command("preleva_da_metodo", "--stato", stdout=out)
    assert "righe" in out.getvalue() and "completato il" in out.getvalue()


def test_limitatore_rispetta_la_banda():
    attese = []
    lim = Limitatore(kb_al_secondo=1, pausa=0, dormi=attese.append)
    lim.consuma([{"testo": "x" * 2000}])  # ~2 KB a 1 KB/s -> ~2 s
    assert 1.5 < attese[0] < 2.5
    lim = Limitatore(kb_al_secondo=0, pausa=0.3, dormi=attese.append)
    lim.consuma([{"a": 1}])
    assert attese[-1] == 0.3


@pytest.mark.django_db
def test_senza_database_indicato(monkeypatch):
    monkeypatch.delenv("METODO_DB_URL", raising=False)
    with pytest.raises(CommandError, match="METODO_DB_URL"):
        call_command("preleva_da_metodo", "--db", "", stdout=StringIO())


@pytest.mark.parametrize(
    ("url", "messaggio"),
    [
        ("metodo", "deve essere postgres://"),
        ("postgres://root:pa@ss!@host:5432/metodo", "%40"),
    ],
)
def test_indirizzo_non_valido_spiegato_senza_password(url, messaggio):
    with pytest.raises(CommandError, match=messaggio) as exc:
        call_command("preleva_da_metodo", "--db", url, "--verifica", stdout=StringIO())
    assert "pa@ss" not in str(exc.value)
