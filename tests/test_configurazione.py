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
    assert ValoreParametro.objects.get(chiave="produzione.prezzo_resa_oraria").aggiornato_da == amministratore


def test_mappatura_gruppi_metodo_coerente_con_la_migrazione():
    import importlib

    from dumetodo.core.permessi import GRUPPI_METODO

    migrazione = importlib.import_module("dumetodo.core.migrations.0002_rinomina_gruppi_metodo")
    assert GRUPPI_METODO == migrazione.RINOMINE


# =========================================================================== casella ordini (IMAP)
@pytest.mark.django_db
def test_password_casella_cifrata_mai_mostrata_ne_storicizzata(client, amministratore):
    from dumetodo.configurazione.models import SegretoParametro, ValoreParametro

    client.force_login(amministratore)
    dati = {
        "gruppo": "CASELLA",
        "casella-casella.server": "imap.esempio.it",
        "casella-casella.porta": "993",
        "casella-casella.ssl": "on",
        "casella-casella.utente": "ordini@esempio.it",
        "casella-casella.password": "Segreta-123",
        "casella-casella.cartella": "INBOX",
        "casella-casella.mittenti_ammessi": "acquisti@cliente.it",
    }
    assert client.post("/configurazione/", dati).status_code == 302
    s = SegretoParametro.objects.get(chiave="casella.password")
    assert "Segreta-123" not in s.cifrato and config.leggi("casella.password") == "Segreta-123"
    assert not ValoreParametro.objects.filter(chiave="casella.password").exists()
    pagina = client.get("/configurazione/").content.decode()
    assert "Segreta-123" not in pagina and "impostata" in pagina
    # password vuota = invariata; "cancella" la toglie
    client.post("/configurazione/", {**dati, "casella-casella.password": ""})
    assert config.leggi("casella.password") == "Segreta-123"
    client.post(
        "/configurazione/", {**dati, "casella-casella.password": "", "casella-casella.password__cancella": "on"}
    )
    assert config.leggi("casella.password") is None


@pytest.mark.django_db
def test_parametri_casella_da_configurazione_o_env(settings):
    from dumetodo.importazione import posta

    settings.ORDINI_IMAP = {"HOST": "env.it", "PORT": 993, "USER": "u", "PASSWORD": "p", "FOLDER": "INBOX", "SSL": True}
    assert posta.parametri_casella()["HOST"] == "env.it"  # nessun server in Configurazione: vale il .env
    config.scrivi("casella.server", "imap.app.it")
    config.scrivi("casella.utente", "ordini@app.it")
    config.scrivi("casella.password", "pw")
    config.invalida_cache()  # nei test la transazione non arriva al commit
    c = posta.parametri_casella()
    assert (c["HOST"], c["USER"], c["PASSWORD"], c["ORIGINE"]) == (
        "imap.app.it",
        "ordini@app.it",
        "pw",
        "Configurazione",
    )
    # valori del form non salvati: password vuota = quella salvata
    c = posta.parametri_casella(modulo={"casella.server": "altro.it", "casella.password": ""})
    assert c["HOST"] == "altro.it" and c["PASSWORD"] == "pw"


@pytest.mark.django_db
def test_prova_connessione(client, amministratore, operatore):
    from dumetodo.importazione import posta

    class Finto:
        def __init__(self, c):
            if c["PASSWORD"] != "giusta":
                raise RuntimeError("authentication failed")

        def select_folder(self, cartella, readonly):
            return {b"EXISTS": 7}

        def logout(self):
            pass

    base = {"HOST": "h", "PORT": 993, "SSL": True, "USER": "u", "FOLDER": "INBOX"}
    assert posta.prova_connessione({**base, "PASSWORD": "giusta"}, client_factory=Finto).ok
    esito = posta.prova_connessione({**base, "PASSWORD": "no"}, client_factory=Finto)
    assert not esito.ok and "authentication failed" in esito.messaggio
    assert "Mancano: password" in posta.prova_connessione({**base, "PASSWORD": ""}).messaggio
    client.force_login(operatore)
    assert client.post("/configurazione/casella/prova/").status_code == 403
    client.force_login(amministratore)
    r = client.post("/configurazione/casella/prova/", {"casella-casella.server": "x"})
    assert r.status_code == 200 and "Mancano" in r.content.decode()


def test_mittenti_non_ammessi_scartati(db):
    from email.message import EmailMessage

    from dumetodo.importazione import posta
    from dumetodo.importazione.models import EsitoMessaggio, MessaggioEmail

    m = EmailMessage()
    m["From"] = "Sconosciuto <spam@altro.it>"
    m["Message-ID"] = "<x1@altro.it>"
    m.set_content("ciao")
    n = posta._elabora_messaggio("c", 1, 1, m.as_bytes(), None, 10**6, ["acquisti@cliente.it"])
    assert n == 0
    r = MessaggioEmail.objects.get()
    assert r.esito == EsitoMessaggio.SCARTATO and "mittente non ammesso" in r.dettaglio


# =========================================================================== logo dell'azienda
def _png(lato=300, colore=(20, 90, 200, 255)):
    import io

    from PIL import Image

    b = io.BytesIO()
    Image.new("RGBA", (lato, lato // 2), colore).save(b, "PNG")
    return b.getvalue()


@pytest.mark.django_db
def test_logo_caricato_normalizzato_e_nel_menu_del_client(client, amministratore):
    import base64
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    client.force_login(amministratore)
    base = {
        "gruppo": "AZIENDA",
        "azienda-azienda.nome_fornitore": "Officine Rossi",
        "azienda-azienda.nome_cliente": "C",
    }
    r = client.post("/configurazione/", {**base, "azienda-azienda.logo": SimpleUploadedFile("logo.png", _png())})
    assert r.status_code == 302
    config.invalida_cache()
    salvato = Image.open(io.BytesIO(base64.b64decode(config.leggi("azienda.logo"))))
    assert salvato.format == "PNG" and max(salvato.size) <= 256

    pagina = client.get("/ordini/").content.decode()
    assert 'class="dm-logo-azienda"' in pagina and "/configurazione/logo.png?v=" in pagina
    r = client.get("/configurazione/logo.png")
    assert r["Content-Type"] == "image/png" and r.content.startswith(b"\x89PNG")
    assert client.get("/configurazione/logo.png", HTTP_IF_NONE_MATCH=r["ETag"]).status_code == 304

    dati = client.get("/client/configurazione.json").json()
    assert dati["intestazione"]["titolo"] == "Officine Rossi"
    for lato in ("16", "24", "32"):
        icona = Image.open(io.BytesIO(base64.b64decode(dati["intestazione"]["loghi"][lato])))
        assert icona.size == (int(lato), int(lato))

    # salvando senza file il logo resta; "Rimuovi" lo toglie
    client.post("/configurazione/", base)
    config.invalida_cache()
    assert config.leggi("azienda.logo")
    client.post("/configurazione/", {**base, "azienda-azienda.logo__cancella": "on"})
    config.invalida_cache()
    assert config.leggi("azienda.logo") is None
    assert client.get("/client/configurazione.json").json()["intestazione"] is None
    assert client.get("/configurazione/logo.png").status_code == 404
    assert "dm-logo-azienda" not in client.get("/ordini/").content.decode()


@pytest.mark.django_db
def test_logo_rifiuta_file_non_immagine(client, amministratore):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client.force_login(amministratore)
    finto = SimpleUploadedFile("logo.png", b"<svg onload=alert(1)>")
    r = client.post("/configurazione/", {"gruppo": "AZIENDA", "azienda-azienda.logo": finto})
    assert r.status_code == 200 and "non è un&#x27;immagine valida" in r.content.decode()
    assert config.leggi("azienda.logo") is None
