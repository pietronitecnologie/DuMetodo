"""Metodo v3, fase 1: clienti committenti, ordini per committente, accessi e selettore."""

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction

from metodo.committenti import servizi
from metodo.committenti.models import Committente
from metodo.consegne import servizi as consegne
from metodo.core.utenti import profilo
from metodo.ordini.models import Ordine, RigaOrdine, StatoOrdine
from metodo.ordini.servizi import OperazioneNonConsentita

from .conftest import _utente


@pytest.fixture
def dmg(db):
    return Committente.objects.get(predefinito=True)


@pytest.fixture
def abb(db):
    return Committente.objects.create(codice="ABB", nome="ABB", colore="#e30613")


def _ordine(committente, numero="0000100", stato=StatoOrdine.INSERITO):
    o = Ordine.objects.create(committente=committente, serie="00040", numero=numero, anno=2026, stato=stato)
    RigaOrdine.objects.create(ordine=o, n_riga="0000001", articolo="ART-A", qta=Decimal(2), prezzo_unitario=1)
    return o


def test_predefinito_creato_e_usato_di_default(dmg):
    assert dmg.predefinito
    o = Ordine.objects.create(serie="00040", numero="1", anno=2026)  # committente non indicato
    assert o.committente == dmg


def test_chiave_unica_per_committente(dmg, abb):
    _ordine(dmg)
    _ordine(abb)  # stesso numero, altro committente: ordini distinti
    with pytest.raises(IntegrityError), transaction.atomic():
        _ordine(dmg)
    with pytest.raises(IntegrityError), transaction.atomic():  # un solo predefinito
        Committente.objects.create(codice="X", nome="X", predefinito=True)


def test_chi_vede_quali_committenti(dmg, abb, operatore, cliente):
    assert set(servizi.accessibili(operatore)) == {dmg, abb}  # interno senza assegnazioni: tutti
    assert not servizi.accessibili(cliente).exists()  # cliente senza assegnazioni, con più committenti
    profilo(cliente).committenti.add(abb)
    assert list(servizi.accessibili(User.objects.get(pk=cliente.pk))) == [abb]
    profilo(operatore).committenti.add(dmg)
    assert list(servizi.accessibili(User.objects.get(pk=operatore.pk))) == [dmg]
    abb.attivo = False
    abb.save()
    assert not servizi.accessibili(User.objects.get(pk=cliente.pk)).exists()


def test_un_solo_committente_come_prima(dmg, cliente):
    assert list(servizi.accessibili(cliente)) == [dmg]


def test_selettore_e_filtri_sugli_ordini(client, dmg, abb, operatore):
    o_dmg, o_abb = _ordine(dmg, "0000100"), _ordine(abb, "0000200")
    client.force_login(operatore)
    pagina = client.get("/ordini/?anno=2026").content.decode()
    assert "0000100" in pagina and "0000200" in pagina and "Tutti i clienti" in pagina
    r = client.post("/committenti/scegli/", {"committente": abb.pk, "torna": "/ordini/?anno=2026"})
    assert r.status_code == 302 and r["Location"] == "/ordini/?anno=2026"
    pagina = client.get("/ordini/?anno=2026").content.decode()
    assert "0000200" in pagina and "0000100" not in pagina
    selezione = client.get("/ordini/seleziona/", {"ordine_cerca": "00"}).content.decode()
    assert "0000200" in selezione and "0000100" not in selezione
    # il dettaglio resta raggiungibile: il selettore restringe gli elenchi, non i permessi
    assert client.get(o_dmg.get_absolute_url()).status_code == 200
    client.post("/committenti/scegli/", {"committente": "", "torna": "https://altro.sito/"})
    assert "0000100" in client.get("/ordini/?anno=2026").content.decode()
    assert client.get(o_abb.get_absolute_url()).status_code == 200


def test_cliente_non_vede_gli_ordini_di_altri_committenti(client, dmg, abb, cliente):
    profilo(cliente).committenti.add(dmg)
    o_dmg, o_abb = _ordine(dmg, "0000100"), _ordine(abb, "0000200")
    client.force_login(cliente)
    assert client.get(o_dmg.get_absolute_url()).status_code == 200
    assert client.get(o_abb.get_absolute_url()).status_code == 404
    assert client.get(f"/note/ordine/{o_abb.pk}/").status_code == 404
    pagina = client.get("/ordini/?anno=2026").content.decode()
    assert "0000100" in pagina and "0000200" not in pagina and "Tutti i clienti" not in pagina
    r = client.post("/committenti/scegli/", {"committente": abb.pk, "torna": "/"}, follow=True)
    assert "non disponibile" in r.content.decode()


def test_una_bolla_e_di_un_solo_committente(client, dmg, abb, operatore):
    _ordine(dmg, "0000100", StatoOrdine.IN_SPEDIZIONE)
    _ordine(abb, "0000200", StatoOrdine.IN_SPEDIZIONE)
    with pytest.raises(OperazioneNonConsentita, match="più clienti committenti"):
        consegne.consegna(operatore, ordini_ids=[])
    client.force_login(operatore)
    client.post("/committenti/scegli/", {"committente": abb.pk, "torna": "/"})
    assert "0000100" not in client.get("/consegne/").content.decode()
    r = client.post("/consegne/consegna/", {"tutto": "1"})
    assert r.status_code == 302
    assert Ordine.objects.get(numero="0000200").stato == StatoOrdine.CONSEGNATO
    assert Ordine.objects.get(numero="0000100").stato == StatoOrdine.IN_SPEDIZIONE


def test_anagrafica_e_predefinito(client, dmg, amministratore, operatore):
    client.force_login(operatore)
    assert client.get("/committenti/").status_code == 403
    client.force_login(amministratore)
    r = client.post("/committenti/nuovo/", {"codice": "abb", "nome": "ABB Italia", "colore": "#e30613", "attivo": "on"})
    assert r.status_code == 302
    abb = Committente.objects.get(codice="ABB")
    client.post("/committenti/", {"predefinito": abb.pk})
    assert Committente.objects.get(predefinito=True) == abb
    r = client.post(f"/committenti/{abb.pk}/", {"codice": "ABB", "nome": "ABB", "colore": "#e30613"})
    assert "non si può disattivare" in r.content.decode()  # il predefinito resta attivo
    assert "ABB Italia" in client.get("/committenti/").content.decode()


def test_committenti_assegnati_dalla_scheda_utente(client, dmg, abb, amministratore):
    client.force_login(amministratore)
    dati = {"azione": "dati", "username": "abb1", "nome": "A", "cognome": "B", "email": "", "cliente": "on"}
    r = client.post("/utenti/nuovo/", {**dati, "password": "Una-Password-Lunga-123"}, follow=True)
    assert "almeno un cliente committente" in r.content.decode()
    client.post("/utenti/nuovo/", {**dati, "password": "Una-Password-Lunga-123", "committente": [abb.pk]})
    u = User.objects.get(username="abb1")
    assert list(servizi.accessibili(u)) == [abb]
    assert "Clienti committenti visibili" in client.get(f"/utenti/{u.pk}/").content.decode()


def test_importazione_sul_predefinito(dmg, abb):
    from .test_importazione import file_txt, importa, riga

    o_abb = _ordine(abb, "0004300")  # stessa chiave del file, altro committente: non deve essere toccato
    o_abb.anno = 2024
    o_abb.save()
    importa(file_txt(riga()))
    assert Ordine.objects.filter(numero="0004300", anno=2024, committente=dmg).count() == 1
    assert o_abb.righe.count() == 1


def test_utente_cliente_creato_da_fixture_con_un_solo_committente(db):
    u = _utente("solo", "Cliente")
    assert servizi.accessibili(u).count() == 1
