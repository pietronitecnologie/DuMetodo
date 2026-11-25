"""Metodo v3, fase 2: il committente in DDT, fatture, report, piano, tracciature e utilità."""

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from metodo.anagrafica.models import Articolo
from metodo.committenti.models import Committente
from metodo.configurazione import servizi as config
from metodo.consegne import servizi as consegne
from metodo.consegne.models import RigaConsegnata
from metodo.core.models import Reparto
from metodo.core.permessi import applica_profilo
from metodo.core.utenti import profilo
from metodo.gestionale import servizi as gestionale
from metodo.ordini.models import Ordine, RigaOrdine, StatoOrdine
from metodo.ordini.selezione import da_codice
from metodo.ordini.servizi import OperazioneNonConsentita
from metodo.pianificazione import piano
from metodo.report import servizi as report
from metodo.tracciatura import servizi as tracciatura
from metodo.utilita import servizi as utilita

from .test_gestionale import dati_fiscali  # noqa: F401  (fixture)

OGGI = timezone.localdate()
DATI_ABB = {
    "gestionale.cliente_denominazione": "ABB S.p.A.",
    "gestionale.cliente_partita_iva": "09876543217",
    "gestionale.cliente_indirizzo": "Via Vittor Pisani 16",
    "gestionale.cliente_cap": "20124",
    "gestionale.cliente_comune": "Milano",
    "gestionale.cliente_provincia": "MI",
    "gestionale.cliente_codice_destinatario": "ABC1234",
}


@pytest.fixture
def dmg(db):
    return Committente.objects.get(predefinito=True)


@pytest.fixture
def abb(db):
    return Committente.objects.create(codice="ABB", nome="ABB", colore="#e30613")


@pytest.fixture
def ufficio(db) -> User:
    u = User.objects.create_user("ufficio", password="password-di-test")
    applica_profilo(u, "Amministrazione")
    return User.objects.get(pk=u.pk)


def _ordine(committente, numero, stato=StatoOrdine.IN_SPEDIZIONE, qta=10, prezzo="2", appendice=""):
    o = Ordine.objects.create(
        committente=committente,
        serie="00040",
        numero=numero,
        appendice=appendice,
        anno=OGGI.year,
        data_scadenza=OGGI,
        stato=stato,
    )
    RigaOrdine.objects.create(
        ordine=o, n_riga="0000001", articolo="P1", qta=Decimal(qta), prezzo_unitario=Decimal(prezzo), data_scadenza=OGGI
    )
    return o


def _consegnato(utente, committente, numero, **kw) -> list[int]:
    o = _ordine(committente, numero, **kw)
    c = consegne.consegna(utente, ordini_ids=[o.pk])
    assert c.committente == committente
    return list(c.righe.values_list("pk", flat=True))


# ----------------------------------------------------------------------------------------- DDT e fatture
def test_ddt_e_fattura_di_un_solo_committente(ufficio, dmg, abb, dati_fiscali):  # noqa: F811
    r_dmg = _consegnato(ufficio, dmg, "0000100")
    r_abb = _consegnato(ufficio, abb, "0000200")
    campi = gestionale.valori_predefiniti_ddt()
    with pytest.raises(OperazioneNonConsentita, match="più clienti committenti"):
        gestionale.emetti_ddt(ufficio, r_dmg + r_abb, OGGI, campi)
    d_dmg = gestionale.emetti_ddt(ufficio, r_dmg, OGGI, campi)
    d_abb = consegne.registra_ddt("A1", OGGI, r_abb, ufficio)
    assert (d_dmg.committente, d_abb.committente) == (dmg, abb)
    with pytest.raises(OperazioneNonConsentita, match="più clienti committenti"):
        gestionale.crea_fattura_differita(ufficio, [d_dmg.pk, d_abb.pk], OGGI)
    f = gestionale.crea_fattura_differita(ufficio, [d_abb.pk], OGGI)
    assert f.committente == abb
    with pytest.raises(OperazioneNonConsentita, match="Cliente"):  # ABB non riceve mai i dati fiscali di DMG
        gestionale.emetti_fattura(ufficio, f.pk)
    config.scrivi_committente(abb, DATI_ABB)
    f = gestionale.emetti_fattura(ufficio, f.pk)
    assert f.cessionario["denominazione"] == "ABB S.p.A."
    assert gestionale.nota_di_credito(ufficio, f.pk).committente == abb


def test_pagine_del_gestionale_per_committente(client, ufficio, dmg, abb, dati_fiscali):  # noqa: F811
    d_dmg = consegne.registra_ddt("D1", OGGI, _consegnato(ufficio, dmg, "0000100"), ufficio)
    d_abb = consegne.registra_ddt("A1", OGGI, _consegnato(ufficio, abb, "0000200"), ufficio)
    f_abb = gestionale.crea_fattura_differita(ufficio, [d_abb.pk], OGGI)
    client.force_login(ufficio)
    pagina = client.get("/gestionale/ddt/").content.decode()
    assert "D1/" in pagina and "A1/" in pagina and "Cliente" in pagina
    client.post("/committenti/scegli/", {"committente": abb.pk, "torna": "/"})
    pagina = client.get("/gestionale/ddt/").content.decode()
    assert "A1/" in pagina and "D1/" not in pagina
    assert "D1/" not in client.get("/consegne/ddt/storico/").content.decode()
    # Il selettore restringe gli elenchi; gli accessi dipendono dai committenti assegnati all'utente.
    assert client.get(f"/gestionale/ddt/{d_dmg.pk}/").status_code == 200
    profilo(ufficio).committenti.add(abb)
    client.force_login(User.objects.get(pk=ufficio.pk))
    assert client.get(f"/gestionale/ddt/{d_dmg.pk}/").status_code == 404
    assert client.get(f"/consegne/ddt/{d_dmg.pk}/file/").status_code == 404
    assert client.get(f"/gestionale/fatture/{f_abb.pk}/").status_code == 200
    # Righe di un altro committente inviate a mano: rifiutate.
    r = client.post(
        "/consegne/ddt/",
        {"numero": "X9", "data_manuale": OGGI.isoformat(), "riga": [str(d_dmg.righe.first().pk)]},
        follow=True,
    )
    assert "non sono disponibili" in r.content.decode()


# ----------------------------------------------------------------------------------------- report e piano
@pytest.fixture
def produzione(db, operatore, dmg, abb):
    config.scrivi("produzione.prezzo_resa_oraria", "10")
    config.invalida_cache()
    rep = Reparto.objects.create(codice="PLS", nome="Pulsanti")
    Articolo.objects.create(codice="P1", reparto=rep, prezzo_riferimento=Decimal("1"))
    _consegnato(operatore, dmg, "0000100", qta=10)
    _consegnato(operatore, abb, "0000200", qta=4)
    _ordine(dmg, "0000101", StatoOrdine.INSERITO, qta=30, appendice="T1")
    _ordine(abb, "0000201", StatoOrdine.INSERITO, qta=50, appendice="T1")


def test_report_per_committente(produzione, dmg, abb):
    settimane = report.settimane_indietro(1, OGGI)
    tutti = report.produttivita(settimane)[0]
    solo_abb = report.produttivita(settimane, [abb.pk], parziale=True)[0]
    assert (tutti.pezzi_totali, solo_abb.pezzi_totali) == (14, 4)
    solo_abb.ore_lavorate = Decimal(10)
    assert solo_abb.efficienza is None and solo_abb.pezzi_ora is None  # ore dell'intera azienda
    assert report.dashboard(committenti=[dmg.pk])["per_stato"][0][1] == 1  # un ordine inserito di DMG
    assert (
        report.controllo_prezzi(report.FiltriPrezzi(solo_differenze=False, dal=None, committenti=[abb.pk])).count() == 1
    )
    assert report.puntualita_arretrato(settimane, [abb.pk])[0]["consegnate"] == 1
    righe, _ = report.materiale_per_reparto(settimane, [abb.pk])
    assert righe[0]["celle"][0]["consegnato"] == Decimal("0.80")  # 4 × 2 € / 10 €/h
    p = piano.calcola(committenti=[abb.pk])
    assert p.righe[0].totale.ore == Decimal("10.00")  # 50 × 2 € / 10 €/h
    assert piano.calcola().righe[0].totale.ore == Decimal("16.00")


def test_pagine_dei_report_con_avviso(client, produzione, amministratore, abb):
    client.force_login(amministratore)
    assert "Dati dei soli ordini" not in client.get("/report/produttivita/").content.decode()
    client.post("/committenti/scegli/", {"committente": abb.pk, "torna": "/"})
    for url in ("/report/", "/report/produttivita/", "/report/materiale/", "/report/prezzi/", "/report/puntualita/"):
        r = client.get(url)
        assert r.status_code == 200 and "Dati dei soli ordini" in r.content.decode(), url
    assert "intera azienda" in client.get("/report/efficienza-reparti/").content.decode()
    assert "Carico dei soli ordini" in client.get("/pianificazione/").content.decode()


# ----------------------------------------------------------------------------------------- tracciature
def test_tracciature_per_committente(amministratore, dmg, abb):
    m = tracciatura.salva_modello(
        amministratore, None, {"nome": "Nota", "categoria": "QUA", "uso_ordine": "F", "uso_riferimento": "F"}
    )
    o_dmg, o_abb = _ordine(dmg, "0000100"), _ordine(abb, "0000200")
    t_dmg = tracciatura.registra(amministratore, m.pk, collegamenti={"ordine": o_dmg}, valori={})
    t_abb = tracciatura.registra(amministratore, m.pk, collegamenti={"ordine": o_abb}, valori={})
    t_libera = tracciatura.registra(amministratore, m.pk, collegamenti={"riferimento": "SN1"}, valori={})
    assert set(tracciatura.cerca({}, [abb.pk])) == {t_abb, t_libera}  # le tracciature senza cliente restano
    assert set(tracciatura.cerca({})) == {t_dmg, t_abb, t_libera}
    with pytest.raises(OperazioneNonConsentita, match="non è di un cliente committente"):
        tracciatura.registra(amministratore, m.pk, collegamenti={"ordine": o_dmg}, valori={}, committenti=[abb.pk])


# ----------------------------------------------------------------------------------------- codici e utilità
def test_stesso_codice_per_due_committenti(amministratore, dmg, abb):
    o_dmg, o_abb = _ordine(dmg, "0004300"), _ordine(abb, "0004300")
    codice = f"40.4300-{OGGI.year}"
    assert da_codice(codice) == o_dmg  # vince il predefinito
    assert da_codice(codice, Ordine.objects.filter(committente=abb)) == o_abb
    _ordine(abb, "0004301")
    # Cambio numero: la chiave deve essere libera solo tra gli ordini dello stesso committente.
    nuovo = utilita.cambia_ordine(
        amministratore, o_dmg.pk, numero="0004301", serie="00040", appendice="", data_scadenza=None, motivo="prova"
    )
    assert nuovo.numero == "0004301"
    with pytest.raises(OperazioneNonConsentita):
        utilita.cambia_ordine(
            amministratore, o_abb.pk, numero="0004301", serie="00040", appendice="", data_scadenza=None, motivo="prova"
        )


def test_migrazione_dei_ddt_esistenti(ufficio, dmg, abb):
    """Le bolle e i DDT prendono il committente dalle righe (come fa la migrazione dei dati esistenti)."""
    righe = _consegnato(ufficio, abb, "0000200")
    d = consegne.registra_ddt("A1", OGGI, righe, ufficio)
    assert RigaConsegnata.objects.get(pk=righe[0]).consegna.committente == abb
    assert d.committente == abb
