import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse

from dumetodo.configurazione import servizi as config
from dumetodo.ordini import servizi
from dumetodo.ordini.models import Ordine, RigaOrdine, StatoOrdine, StatoRiga, StoricoScadenza
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


def test_vincolo_db_una_sola_riga_attiva_per_numero(ordine):
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        RigaOrdine.objects.create(ordine=ordine, n_riga="0000001", articolo="X", qta=1)


def test_colore_cambia_a_ogni_cambio_articolo():
    from types import SimpleNamespace

    from dumetodo.ordini.aiuti_visivi import PALETTE_CAMBIO_ARTICOLO as P
    from dumetodo.ordini.aiuti_visivi import colora_cambi_articolo

    def righe():
        return [SimpleNamespace(articolo=a) for a in ["A", "A", "B", "A", "C", "D", "E", "F", "G"]]

    colorate = colora_cambi_articolo(righe())
    assert [r.colore for r in colorate] == [P[0], P[0], P[1], P[2], P[3], P[4], P[5], P[0], P[1]]
    assert [r.cambio_articolo for r in colorate][:4] == [True, False, True, True]
    # Deterministico: in Metodo il contatore era globale e i colori cambiavano a ogni apertura.
    assert [r.colore for r in colora_cambi_articolo(righe())] == [r.colore for r in colorate]


def test_dettaglio_mostra_i_colori_degli_articoli(client, ordine, operatore):
    from dumetodo.ordini.aiuti_visivi import PALETTE_CAMBIO_ARTICOLO as P

    client.force_login(operatore)
    html = client.get(f"/ordini/{ordine.pk}/").content.decode()
    assert f'style="color: {P[0]}" title="Nessuna descrizione in anagrafica">ART-A' in html
    assert f'style="color: {P[1]}"' in html


# =========================================================================== selezione univoca dell'ordine
def test_selezione_ordine_cerca_per_parole(db):
    from dumetodo.ordini import selezione

    a = Ordine.objects.create(serie="00040", numero="0004300", anno=2026)
    b = Ordine.objects.create(serie="00040", numero="0004300", appendice="T1", anno=2026)
    c = Ordine.objects.create(serie="00041", numero="0004300", anno=2025)
    Ordine.objects.create(serie="00040", numero="0005555", anno=2026)
    assert set(selezione.cerca("4300")) == {a, b, c}
    assert list(selezione.cerca("4300 T1")) == [b]
    assert set(selezione.cerca("40 4300 2026")) == {a, b}
    assert list(selezione.cerca("4300 2025")) == [c]
    assert set(selezione.cerca("")) == set(Ordine.objects.all())  # vuoto: ordini aperti
    assert selezione.da_codice("40.0004300.T1-2026") == b
    assert selezione.da_codice("00040.0004300-2026") == a
    assert selezione.da_codice("4300") is None


def test_campo_ordine_accetta_chiave_o_codice(db):
    from django import forms

    from dumetodo.ordini.selezione import CampoOrdine

    o = Ordine.objects.create(serie="00040", numero="0004300", anno=2026)

    class F(forms.Form):
        ordine = CampoOrdine()

    assert F({"ordine": str(o.pk)}).is_valid()
    f = F({"ordine": "", "ordine_cerca": "40.0004300-2026"})
    assert f.is_valid() and f.cleaned_data["ordine"] == o
    f = F({"ordine": "999999"})
    assert not f.is_valid() and "non trovato" in str(f.errors)
    html = str(F(initial={"ordine": o.pk})["ordine"])
    assert f'value="{o.pk}"' in html and "0004300" in html and 'name="ordine_cerca"' in html


def test_endpoint_selezione(client, operatore, db):
    Ordine.objects.create(serie="00040", numero="0004300", appendice="T1", anno=2026)
    url = reverse("ordini:seleziona")
    assert client.get(url).status_code == 302
    client.force_login(operatore)
    r = client.get(url, {"campo": "ordine", "ordine_cerca": "4300"})
    assert r.status_code == 200 and "data-dm-ordine-pk" in r.content.decode() and "T1" in r.content.decode()
    assert "Nessun ordine" in client.get(url, {"ordine_cerca": "999"}).content.decode()
