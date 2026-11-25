"""Metodo v3, fase 4: flusso produttivo di ogni cliente committente."""

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from metodo.committenti.models import Committente
from metodo.configurazione import servizi as config
from metodo.consegne import servizi as consegne
from metodo.core.models import Reparto
from metodo.core.permessi import applica_profilo
from metodo.ordini import servizi as ordini
from metodo.ordini.models import Ordine, RigaOrdine, StatoOrdine
from metodo.ordini.servizi import OperazioneNonConsentita
from metodo.tracciatura import servizi as tracciatura

OGGI = timezone.localdate()


@pytest.fixture
def dmg(db):
    return Committente.objects.get(predefinito=True)


@pytest.fixture
def abb(db):
    return Committente.objects.create(codice="ABB", nome="ABB")


@pytest.fixture
def capo(db) -> User:
    u = User.objects.create_user("capo")
    applica_profilo(u, "Amministratore")
    return User.objects.get(pk=u.pk)


def _ordine(committente, numero="0000100", articolo="18090054", stato=StatoOrdine.INSERITO, bloccata=False):
    o = Ordine.objects.create(committente=committente, serie="00040", numero=numero, anno=OGGI.year, stato=stato)
    RigaOrdine.objects.create(
        ordine=o, n_riga="0000001", articolo=articolo, qta=Decimal(2), prezzo_unitario=Decimal(1), bloccata=bloccata
    )
    RigaOrdine.objects.create(ordine=o, n_riga="0000002", articolo="ALTRO", qta=Decimal(1), prezzo_unitario=1)
    return o


def _collaudo(capo, **extra):
    return tracciatura.salva_modello(
        capo,
        None,
        {"nome": "Collaudo", "categoria": "QUA", "uso_ordine": "F", "codici": "18090*", "richiesta": "SPE", **extra},
    )


# ----------------------------------------------------------------------------------------- passaggi del flusso
def test_lavorato_con_mancanze_e_consegne_parziali_per_committente(capo, dmg, abb):
    config.scrivi_committente(abb, {"flusso.lavorato_con_mancanze": False, "flusso.consegna_parziale": False})
    o_abb, o_dmg = _ordine(abb, bloccata=True), _ordine(dmg, "0000200", bloccata=True)
    assert StatoOrdine.IN_LAVORAZIONE not in {t.a for t in ordini.transizioni_ammesse(o_abb, capo)}
    assert StatoOrdine.IN_LAVORAZIONE in {t.a for t in ordini.transizioni_ammesse(o_dmg, capo)}
    with pytest.raises(OperazioneNonConsentita, match="Lavorato con mancanze"):
        ordini.cambia_stato(o_abb.pk, StatoOrdine.IN_LAVORAZIONE, capo)
    with pytest.raises(OperazioneNonConsentita, match="consegne parziali"):
        ordini.cambia_stato(o_abb.pk, StatoOrdine.IN_SPEDIZIONE, capo)
    # DMG segue il flusso generale: con righe bloccate diventa evasione parziale.
    assert ordini.cambia_stato(o_dmg.pk, StatoOrdine.IN_SPEDIZIONE, capo).stato == StatoOrdine.EVASIONE_PARZIALE
    RigaOrdine.objects.filter(ordine=o_abb).update(bloccata=False)
    assert ordini.cambia_stato(o_abb.pk, StatoOrdine.IN_SPEDIZIONE, capo).stato == StatoOrdine.IN_SPEDIZIONE


# ----------------------------------------------------------------------------------------- tracciature obbligatorie
def test_tracciatura_obbligatoria_prima_della_spedizione(capo, dmg, abb):
    m = _collaudo(capo, committenti=[abb.pk])
    o_abb, o_dmg = _ordine(abb), _ordine(dmg, "0000200")
    assert tracciatura.mancanti(o_abb, "SPE") == [m]
    assert tracciatura.mancanti(o_dmg, "SPE") == []  # il modello è solo del flusso di ABB
    with pytest.raises(OperazioneNonConsentita, match="servono le tracciature «Collaudo»"):
        ordini.cambia_stato(o_abb.pk, StatoOrdine.IN_SPEDIZIONE, capo)
    t = tracciatura.registra(capo, m.pk, collegamenti={"ordine": o_abb}, valori={})
    assert ordini.cambia_stato(o_abb.pk, StatoOrdine.IN_SPEDIZIONE, capo).stato == StatoOrdine.IN_SPEDIZIONE
    # Annullata la tracciatura, la consegna si ferma (le obbligatorie per la spedizione valgono anche qui).
    tracciatura.annulla(capo, t.pk, "rifatta")
    with pytest.raises(OperazioneNonConsentita, match="prima di consegnare"):
        consegne.consegna(capo, ordini_ids=[o_abb.pk])


def test_tracciatura_obbligatoria_solo_per_i_suoi_codici_e_spegnibile(capo, abb):
    _collaudo(capo, richiesta="CON")
    senza_codice = _ordine(abb, articolo="99999")
    assert tracciatura.mancanti(senza_codice, "CON") == []
    o = _ordine(abb, "0000200", stato=StatoOrdine.IN_SPEDIZIONE)
    assert tracciatura.mancanti(o, "SPE") == []  # richiesta solo prima della consegna
    with pytest.raises(OperazioneNonConsentita, match="prima di consegnare"):
        consegne.consegna(capo, ordini_ids=[o.pk])
    config.scrivi_committente(abb, {"flusso.tracciature_obbligatorie": False})
    assert consegne.consegna(capo, ordini_ids=[o.pk]).righe.count() == 2


def test_non_conforme_non_basta_e_modello_senza_ordine(capo, abb):
    m = _collaudo(capo)
    campo = tracciatura.salva_campo(
        capo, m.pk, None, {"etichetta": "Coppia", "tipo": "DEC", "minimo": "1", "massimo": "2", "obbligatorio": True}
    )
    o = _ordine(abb)
    tracciatura.registra(capo, m.pk, collegamenti={"ordine": o}, valori={f"campo_{campo.pk}": "5"})  # fuori limiti
    assert tracciatura.mancanti(o, "SPE") == [m]
    with pytest.raises(OperazioneNonConsentita, match="collegare all'ordine"):
        tracciatura.salva_modello(capo, m.pk, {"nome": "Collaudo", "uso_ordine": "", "richiesta": "SPE"})


def test_pannello_e_pagina_del_modello(client, capo, dmg, abb):
    m = _collaudo(capo)
    o = _ordine(abb)
    client.force_login(capo)
    assert "Obbligatorie, da fare" in client.get(f"/tracciatura/pannello/?ordine={o.pk}").content.decode()
    pagina = client.get(m.get_absolute_url()).content.decode()
    assert "Obbligatoria prima della spedizione" in pagina and "Clienti committenti" in pagina
    client.post(
        m.get_absolute_url(),
        {"azione": "modello", "nome": "Collaudo", "categoria": "QUA", "uso_ordine": "F", "committente": [dmg.pk]},
    )
    m.refresh_from_db()
    assert list(m.committenti.all()) == [dmg] and m.richiesta == ""
    assert tracciatura.modelli_per(["18090054"], committente_id=abb.pk) == []


# ----------------------------------------------------------------------------------------- reparti
def test_reparti_del_committente(client, amministratore, dmg, abb):
    pls = Reparto.objects.create(codice="PLS", nome="Pulsanti")
    cab = Reparto.objects.create(codice="CAB", nome="Cablaggio ABB")
    comune = Reparto.objects.create(codice="MAG", nome="Magazzino")
    client.force_login(amministratore)
    r = client.post(
        f"/reparti/{cab.pk}/", {"codice": "CAB", "nome": "Cablaggio ABB", "committenti": [abb.pk], "attivo": "on"}
    )
    assert r.status_code == 302 and list(cab.committenti.all()) == [abb]
    pls.committenti.add(dmg)
    client.post("/committenti/scegli/", {"committente": abb.pk, "torna": "/"})
    pagina = client.get("/pianificazione/tabulati/settimanale/").content.decode()
    assert "Cablaggio ABB" in pagina and comune.nome in pagina and "Pulsanti" not in pagina
