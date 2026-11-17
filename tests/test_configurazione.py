import json
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command

from dumetodo.configurazione import servizi as config
from dumetodo.configurazione.models import ValoreParametro
from dumetodo.configurazione.registro import REGISTRO
from dumetodo.core.models import Reparto


def test_decimale_accetta_formato_italiano():
    p = REGISTRO["produzione.prezzo_resa_oraria"]
    assert p.converti("1.234,56") == Decimal("1234.56")
    assert p.converti("45.5") == Decimal("45.5")
    with pytest.raises(ValidationError):
        p.converti("abc")
    with pytest.raises(ValidationError):
        p.converti("0")  # minimo 0,01: evita divisioni per zero nei calcoli delle ore


def test_lista_email_valida_e_deduplica():
    p = REGISTRO["email.cliente"]
    assert p.converti("a@x.it; b@x.it, A@x.it") == ["a@x.it", "b@x.it"]
    with pytest.raises(ValidationError):
        p.converti("non-una-email")


@pytest.mark.django_db
def test_leggi_restituisce_default_senza_valore():
    assert config.leggi("ordini.soglia_pezzi_commesse_grandi") == 60
    assert config.leggi("email.cliente") == []
    assert config.leggi("produzione.prezzo_resa_oraria") is None


@pytest.mark.django_db
def test_scrivi_molti_e_tutto_o_niente():
    with pytest.raises(ValidationError):
        config.scrivi_molti({"ordini.serie_fast": "44", "produzione.prezzo_resa_oraria": "zero"})
    assert not ValoreParametro.objects.exists()
    config.scrivi_molti({"ordini.serie_fast": "44", "produzione.prezzo_resa_oraria": "50,5"})
    config.invalida_cache()
    assert config.leggi("ordini.serie_fast") == "44"
    assert config.leggi("produzione.prezzo_resa_oraria") == Decimal("50.5")


@pytest.mark.django_db
def test_chiave_sconosciuta_e_un_errore_di_programmazione():
    with pytest.raises(KeyError):
        config.leggi("NON_ESISTE")


@pytest.mark.django_db
def test_richiedi_segnala_parametro_mancante():
    with pytest.raises(config.ParametroNonConfigurato):
        config.richiedi("produzione.prezzo_resa_oraria")


@pytest.mark.django_db
def test_valore_corrotto_nel_db_non_rompe_la_lettura():
    ValoreParametro.objects.create(chiave="ordini.soglia_pezzi_commesse_grandi", valore="non-numero")
    config.invalida_cache()
    assert config.leggi("ordini.soglia_pezzi_commesse_grandi") == 60


@pytest.mark.django_db
def test_import_configurazione_legacy(tmp_path, capsys):
    Reparto.objects.create(codice="GK01-PLS", nome="Pulsanti")
    righe = [
        {"chiave": "PREZZO_RESA_ORARIA", "valore": "45,50"},
        {"chiave": "EMAIL_METODO_ADDRESS", "valore": "metodo@example.com"},
        {"chiave": "EMAIL_MAGAZZINO_DMG_1", "valore": "m1@example.com"},
        {"chiave": "EMAIL_MAGAZZINO_DMG_2", "valore": "m2@example.com"},
        {"chiave": "EMAIL_METODO_PASSWORD", "valore": "segreta"},
        {"chiave": "ID_EMAIL", "valore": "123"},
        {"chiave": "SOGLIA_PULSANTI", "valore": "120,5"},
    ]
    f = tmp_path / "config.json"
    f.write_text(json.dumps(righe))

    call_command("importa_configurazione_metodo", str(f))  # prova a secco
    assert not ValoreParametro.objects.exists()

    call_command("importa_configurazione_metodo", str(f), "--applica")
    config.invalida_cache()
    assert config.leggi("produzione.prezzo_resa_oraria") == Decimal("45.50")
    assert config.leggi("email.magazzino_cliente") == ["m1@example.com", "m2@example.com"]
    assert Reparto.objects.get(nome="Pulsanti").soglia_ore_settimanali == Decimal("120.5")
    assert "segreta" not in json.dumps(list(ValoreParametro.objects.values_list("valore", flat=True)))
    assert "EMAIL_HOST_PASSWORD" in capsys.readouterr().out


@pytest.mark.django_db
def test_pagina_configurazione_solo_admin(client, operatore, amministratore):
    client.force_login(operatore)
    assert client.get("/configurazione/").status_code == 403
    client.force_login(amministratore)
    assert client.get("/configurazione/").status_code == 200
    r = client.post("/configurazione/", {"gruppo": "PRODUZIONE", "produzione-produzione.prezzo_resa_oraria": "48,2"})
    assert r.status_code == 302
    config.invalida_cache()
    assert config.leggi("produzione.prezzo_resa_oraria") == Decimal("48.2")
    assert ValoreParametro.objects.get().aggiornato_da == amministratore


def test_mappatura_gruppi_metodo_coerente_con_la_migrazione():
    import importlib

    from dumetodo.core.ruoli import GRUPPI_METODO

    migrazione = importlib.import_module("dumetodo.core.migrations.0002_rinomina_gruppi_metodo")
    assert {vecchio: ruolo.value for vecchio, ruolo in GRUPPI_METODO.items()} == migrazione.RINOMINE
