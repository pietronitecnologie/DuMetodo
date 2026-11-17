import datetime as dt
from decimal import Decimal

import pytest

from dumetodo.configurazione import servizi as config
from dumetodo.ordini import servizi
from dumetodo.ordini.models import RigaOrdine, StatoOrdine, StatoRiga, StoricoScadenza
from dumetodo.ordini.servizi import OperazioneNonConsentita


def test_transizioni_valide(ordine, operatore):
    o = servizi.cambia_stato(ordine.pk, StatoOrdine.IN_LAVORAZIONE, operatore)
    assert o.stato == StatoOrdine.IN_LAVORAZIONE
    o = servizi.cambia_stato(ordine.pk, StatoOrdine.IN_SPEDIZIONE, operatore)
    assert o.stato == StatoOrdine.IN_SPEDIZIONE


@pytest.mark.parametrize("stato", ["", "XXX", None])
def test_stato_non_valido_rifiutato(ordine, operatore, stato):
    with pytest.raises(OperazioneNonConsentita):
        servizi.cambia_stato(ordine.pk, stato, operatore)


def test_annullamento_e_consegna_forzata_solo_admin(ordine, operatore, amministratore):
    for stato in (StatoOrdine.ANNULLATO, StatoOrdine.CONSEGNATO):
        with pytest.raises(OperazioneNonConsentita, match="permessi"):
            servizi.cambia_stato(ordine.pk, stato, operatore)
    o = servizi.cambia_stato(ordine.pk, StatoOrdine.CONSEGNATO, amministratore)
    assert o.data_consegna == dt.date.today()


def test_cliente_non_cambia_stato(ordine, cliente):
    with pytest.raises(OperazioneNonConsentita):
        servizi.cambia_stato(ordine.pk, StatoOrdine.IN_LAVORAZIONE, cliente)


def test_transizione_inesistente(ordine, amministratore):
    servizi.cambia_stato(ordine.pk, StatoOrdine.ANNULLATO, amministratore)
    with pytest.raises(OperazioneNonConsentita, match="Non è possibile"):
        servizi.cambia_stato(ordine.pk, StatoOrdine.IN_SPEDIZIONE, amministratore)


def test_spedisci_con_riga_bloccata_diventa_evasione_parziale(ordine, operatore):
    bloccata = ordine.righe.get(n_riga="0000001")
    servizi.azione_riga(bloccata.pk, "blocca", operatore)
    o = servizi.cambia_stato(ordine.pk, StatoOrdine.IN_SPEDIZIONE, operatore)
    assert o.stato == StatoOrdine.EVASIONE_PARZIALE
    assert ordine.righe.get(n_riga="0000002").in_evasione
    assert not ordine.righe.get(n_riga="0000001").in_evasione


def test_azioni_riga_non_toccano_lo_stato_ordine(ordine, operatore):
    # In Metodo "Blocca" salvava la riga e poi andava in errore 500 impostando lo stato a vuoto.
    r = ordine.righe.first()
    servizi.azione_riga(r.pk, "blocca", operatore)
    servizi.azione_riga(r.pk, "scorpora", operatore)
    ordine.refresh_from_db()
    r.refresh_from_db()
    assert ordine.stato == StatoOrdine.INSERITO and r.bloccata and r.scorporata


def test_evadi_porta_l_ordine_in_evasione_parziale(ordine, operatore):
    servizi.azione_riga(ordine.righe.first().pk, "evadi", operatore)
    ordine.refresh_from_db()
    assert ordine.stato == StatoOrdine.EVASIONE_PARZIALE


def test_annulla_riga_solo_admin(ordine, operatore, amministratore):
    r = ordine.righe.first()
    with pytest.raises(OperazioneNonConsentita):
        servizi.azione_riga(r.pk, "annulla", operatore)
    servizi.azione_riga(r.pk, "annulla", amministratore)
    r.refresh_from_db()
    assert r.stato == StatoRiga.ANNULLATA


def test_righe_in_spedizione_non_modificabili_da_operatore(ordine, operatore):
    servizi.cambia_stato(ordine.pk, StatoOrdine.IN_SPEDIZIONE, operatore)
    with pytest.raises(OperazioneNonConsentita):
        servizi.azione_riga(ordine.righe.first().pk, "blocca", operatore)


def test_dividi_riga(ordine, caporeparto, operatore):
    r = ordine.righe.get(n_riga="0000001")
    with pytest.raises(OperazioneNonConsentita):
        servizi.dividi_riga(r.pk, "3", operatore)
    for sbagliata in ("0", "10", "11", "-1", "abc"):
        with pytest.raises(OperazioneNonConsentita):
            servizi.dividi_riga(r.pk, sbagliata, caporeparto)
    nuova = servizi.dividi_riga(r.pk, "3,5", caporeparto, dt.date(2026, 5, 1))
    r.refresh_from_db()
    assert (r.qta, nuova.qta, nuova.n_riga) == (Decimal("6.5"), Decimal("3.5"), "0000001/2")
    assert servizi.dividi_riga(r.pk, "1", caporeparto).n_riga == "0000001/3"
    assert StoricoScadenza.objects.filter(riga=nuova, nuova=dt.date(2026, 5, 1)).exists()
    assert servizi.totale_pezzi(ordine) == 15  # la divisione non cambia il totale


def test_cambia_scadenza_richiede_motivo_e_storicizza(ordine, caporeparto):
    with pytest.raises(OperazioneNonConsentita):
        servizi.cambia_scadenza_ordine(ordine.pk, dt.date(2026, 6, 1), " ", caporeparto)
    servizi.cambia_scadenza_ordine(ordine.pk, dt.date(2026, 6, 1), "Materiale in ritardo", caporeparto)
    s = StoricoScadenza.objects.get(riga=None)
    assert (s.precedente, s.nuova, s.utente) == (dt.date(2026, 4, 2), dt.date(2026, 6, 1), caporeparto)
    assert set(ordine.righe.values_list("data_scadenza", flat=True)) == {dt.date(2026, 6, 1)}


def test_ore_teoriche(ordine):
    assert servizi.ore_teoriche(ordine) is None  # resa non configurata: niente 500
    config.scrivi("produzione.prezzo_resa_oraria", "10")
    config.invalida_cache()
    assert servizi.ore_teoriche(ordine) == Decimal("3.00")  # (10*2.5 + 5*1) / 10


def test_attiva_produzione(ordine, operatore):
    assert servizi.attiva_produzione(dt.date(2026, 4, 1), dt.date(2026, 4, 30), operatore) == 1
    ordine.refresh_from_db()
    assert ordine.stato == StatoOrdine.IN_LAVORAZIONE


def test_vincolo_db_una_sola_riga_attiva_per_numero(ordine):
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        RigaOrdine.objects.create(ordine=ordine, n_riga="0000001", articolo="X", qta=1)
