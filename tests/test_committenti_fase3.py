"""Metodo v3, fase 3: impostazioni proprie di ogni cliente committente."""

import datetime as dt
from decimal import Decimal
from email.message import EmailMessage

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from metodo.committenti import servizi as committenti
from metodo.committenti.models import Committente
from metodo.comunicazioni.models import EmailInUscita
from metodo.configurazione import servizi as config
from metodo.consegne import servizi as consegne
from metodo.core.permessi import applica_profilo
from metodo.gestionale import servizi as gestionale
from metodo.importazione import posta
from metodo.importazione import servizi as importazione
from metodo.importazione.models import EsitoMessaggio, FileImportato, MessaggioEmail
from metodo.ordini.models import Ordine, RigaOrdine, StatoOrdine

from .test_committenti_fase2 import DATI_ABB
from .test_gestionale import dati_fiscali  # noqa: F401  (fixture)
from .test_importazione import file_txt, riga
from .test_posta import IMAP, FakeIMAP

OGGI = timezone.localdate()
BOLLA_ABB = "nome: ABB\nestensione: txt\nnome_file: BOLLA_ABB\n\n[riga]\n{riga.articolo};{riga.qta|intero}\n"


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


def _in_spedizione(committente, numero="0000200"):
    o = Ordine.objects.create(
        committente=committente, serie="00040", numero=numero, anno=OGGI.year, stato=StatoOrdine.IN_SPEDIZIONE
    )
    RigaOrdine.objects.create(ordine=o, n_riga="0000001", articolo="P1", qta=Decimal(3), prezzo_unitario=Decimal(2))
    return o


# ----------------------------------------------------------------------------------------- lettura dei valori
def test_valori_propri_ereditati_e_riservati(dmg, abb):
    config.scrivi("consegna.tracciato_bolla", BOLLA_ABB.replace("ABB", "GEN"))
    config.scrivi("gestionale.cliente_denominazione", "DMG S.r.l.")
    config.scrivi("gestionale.pagamento_giorni", "30")
    config.invalida_cache()
    # EREDITA: senza un valore suo vale il generale.
    assert config.leggi("gestionale.pagamento_giorni", abb) == 30
    assert "GEN" in config.leggi("consegna.tracciato_bolla", abb)
    # PROPRIO: i dati fiscali generali sono del predefinito, mai di ABB.
    assert config.leggi("gestionale.cliente_denominazione", dmg) == "DMG S.r.l."
    assert config.leggi("gestionale.cliente_denominazione", abb) is None
    assert gestionale.dati_cliente(abb)["denominazione"] == "ABB"  # il nome del committente, niente di DMG
    config.scrivi_committente(abb, {"gestionale.pagamento_giorni": "90", "consegna.tracciato_bolla": BOLLA_ABB})
    assert config.leggi("gestionale.pagamento_giorni", abb) == 90
    assert config.leggi("gestionale.pagamento_giorni", abb.pk) == 90  # anche con l'id
    assert config.leggi("gestionale.pagamento_giorni") == 30
    config.scrivi_committente(abb, {"gestionale.pagamento_giorni": None})  # vuoto: torna a ereditare
    assert config.leggi("gestionale.pagamento_giorni", abb) == 30
    with pytest.raises(ValidationError):
        config.scrivi_committente(abb, {"consegna.tracciato_bolla": "[sezione_sconosciuta]\n"})
    with pytest.raises(ValueError):
        config.scrivi_committente(abb, {"azienda.nome_fornitore": "X"})  # non si imposta per committente


def test_cambio_predefinito(dmg, abb, amministratore):
    config.scrivi("gestionale.cliente_denominazione", "Dati generali")
    config.invalida_cache()
    assert config.leggi("gestionale.cliente_denominazione", abb) is None
    committenti.imposta_predefinito(amministratore, abb.pk)
    config.invalida_cache()
    assert config.leggi("gestionale.cliente_denominazione", abb) == "Dati generali"
    assert config.leggi("gestionale.cliente_denominazione", dmg) is None


# ----------------------------------------------------------------------------------------- documenti
def test_bolla_file_tecnico_etichette_e_ddt_del_committente(ufficio, dmg, abb, dati_fiscali):  # noqa: F811
    config.scrivi_committente(
        abb,
        {
            **DATI_ABB,
            "consegna.tracciato_bolla": BOLLA_ABB,
            "email.file_tecnico": "ddt@abb.example",
            "consegna.codice_fornitore_etichette": "F-ABB-77",
            "gestionale.ddt_porto": "Assegnato",
        },
    )
    o = _in_spedizione(abb)
    c = consegne.consegna(ufficio, ordini_ids=[o.pk])
    assert c.file_nome == "BOLLA_ABB.txt" and bytes(c.file_contenuto).startswith(b"P1;3")
    import io

    from openpyxl import load_workbook

    assert load_workbook(io.BytesIO(consegne.etichette_xlsx([o]))).active.cell(1, 9).value == "F-ABB-77"
    righe = list(c.righe.values_list("pk", flat=True))
    assert gestionale.valori_predefiniti_ddt(abb)["porto"] == "Assegnato"
    d = gestionale.emetti_ddt(ufficio, righe, OGGI, gestionale.valori_predefiniti_ddt(abb))
    assert d.documento["destinatario"]["denominazione"] == "ABB S.p.A."
    assert d.email is not None and d.email.destinatari == ["ddt@abb.example"]
    # Il predefinito continua a usare i valori generali.
    assert gestionale.dati_cliente(dmg)["denominazione"] == "Cliente & Figli S.p.A."


def test_ddt_di_un_committente_senza_dati_fiscali(ufficio, abb, dati_fiscali):  # noqa: F811
    o = _in_spedizione(abb)
    righe = list(consegne.consegna(ufficio, ordini_ids=[o.pk]).righe.values_list("pk", flat=True))
    with pytest.raises(Exception, match="Cliente: indirizzo e comune mancanti"):
        gestionale.emetti_ddt(ufficio, righe, OGGI, gestionale.valori_predefiniti_ddt(abb))


def test_email_delle_note_al_cliente_del_committente(abb, configura_email, operatore):
    from metodo.comunicazioni import servizi as note

    config.scrivi("note.notifica_email", True)
    config.scrivi_committente(abb, {"email.cliente": "note@abb.example"})
    config.invalida_cache()
    o = _in_spedizione(abb)
    note.aggiungi_nota(o, operatore, "Pronto domani", "CON")
    assert EmailInUscita.objects.filter(riferimento__startswith="nota:").get().destinatari == ["note@abb.example"]


# ----------------------------------------------------------------------------------------- ordini in arrivo
def _mail(mittente: str, message_id: str, numero="0004300") -> bytes:
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = mittente, "Ordine", message_id
    m.set_content("in allegato")
    m.add_attachment(file_txt(riga(ordine=numero)), maintype="text", subtype="plain", filename=f"OCL_{numero}_40.TXT")
    return m.as_bytes()


@pytest.mark.django_db(transaction=True)
@override_settings(ORDINI_IMAP=IMAP)
def test_casella_generale_smista_per_mittente():
    from metodo.committenti.models import predefinito_id
    from metodo.importazione.models import StatoCasella

    dmg = Committente.objects.get(pk=predefinito_id())  # anche dopo lo svuotamento dei test con transazioni
    abb = Committente.objects.create(codice="ABB", nome="ABB")
    StatoCasella.objects.create(  # casella già letta: si prendono i messaggi arrivati da ieri
        casella=f"{IMAP['USER']}@{IMAP['HOST']}/{IMAP['FOLDER']}", letto_fino_al=timezone.now() - dt.timedelta(days=1)
    )
    config.scrivi("casella.mittenti_ammessi", "ordini@dmg.example")
    config.scrivi_committente(abb, {"casella.mittenti_ammessi": "acquisti@abb.example"})
    config.invalida_cache()
    fake = FakeIMAP(
        {
            1: _mail("ordini@dmg.example", "<1@x>"),
            2: _mail("Acquisti <acquisti@abb.example>", "<2@x>"),  # stesso numero d'ordine, altro cliente
            3: _mail("spam@altro.example", "<3@x>"),
        }
    )
    esito = posta.leggi_casella(client_factory=lambda: fake)
    assert esito.file == 2
    assert set(Ordine.objects.values_list("committente__codice", "numero")) == {
        (dmg.codice, "0004300"),
        (abb.codice, "0004300"),
    }
    assert MessaggioEmail.objects.get(uid=3).esito == EsitoMessaggio.SCARTATO


def test_casella_propria_del_committente(dmg, abb):
    assert [c for c, _ in posta.caselle()] == []  # nessuna casella (né generale né di ABB)
    config.scrivi_committente(
        abb,
        {
            "casella.server": "imap.abb.example",
            "casella.utente": "ordini-abb",
            "casella.password": "segreta",
        },
    )
    elenco = posta.caselle()
    assert [c for c, _ in elenco] == [abb]
    assert elenco[0][1]["HOST"] == "imap.abb.example" and elenco[0][1]["PASSWORD"] == "segreta"
    assert config.leggi("casella.password") is None  # la password di ABB non è quella generale
    instrada = posta.instradamento_proprio(abb, [])
    assert instrada("chiunque@example.com") == abb.pk
    n = posta._elabora_messaggio("abb", 1, 1, _mail("x@abb.example", "<a@x>"), None, 10**6, instrada=instrada)
    assert n == 1 and Ordine.objects.get().committente == abb


def test_caricamento_manuale_per_committente(client, dmg, abb, amministratore):
    client.force_login(amministratore)
    from django.core.files.uploadedfile import SimpleUploadedFile

    file = SimpleUploadedFile("OCL_4300_40.TXT", file_txt(riga()))
    r = client.post("/importazione/", {"file": file, "committente": abb.pk})
    assert r.status_code == 200
    assert Ordine.objects.get().committente == abb
    # Lo stesso file per l'altro committente non è un duplicato.
    f = importazione.importa(importazione.registra_file("OCL_4300_40.TXT", file_txt(riga()), "UPL", committente=dmg))
    assert f.ordini_creati == 1 and Ordine.objects.count() == 2
    assert FileImportato.objects.filter(committente=abb).count() == 1


# ----------------------------------------------------------------------------------------- pagina
def test_pagina_impostazioni_del_committente(client, dmg, abb, amministratore, ufficio):
    client.force_login(amministratore)
    url = f"/committenti/{abb.pk}/impostazioni/"
    pagina = client.get(url).content.decode()
    assert "Casella ordini" in pagina and "Dati fiscali del Cliente" not in pagina  # dati fiscali: Gestionale
    r = client.post(
        url,
        {
            "gruppo": "CONSEGNA",
            "consegna-consegna.codice_fornitore_etichette": "F-77",
            "consegna-consegna.tracciato_bolla": "",
        },
    )
    assert r.status_code == 302
    assert config.leggi("consegna.codice_fornitore_etichette", abb) == "F-77"
    assert not config.ha_valore_proprio("consegna.tracciato_bolla", abb)
    r = client.post(url, {"gruppo": "CONSEGNA", "consegna-consegna.tracciato_bolla": "[sezione_sconosciuta]"})
    assert "da correggere" in r.content.decode()
    # Il predefinito non ha la casella propria; i dati fiscali li vede solo chi ha il Gestionale.
    assert "Casella ordini" not in client.get(f"/committenti/{dmg.pk}/impostazioni/").content.decode()
    # Con anche il profilo Amministrazione (Gestionale) compaiono i dati fiscali del committente.
    applica_profilo(amministratore, "Amministratore", "Amministrazione")
    client.force_login(User.objects.get(pk=amministratore.pk))
    pagina = client.get(url).content.decode()
    assert "Dati fiscali del Cliente" in pagina and "Casella ordini" in pagina
    r = client.post(
        url, {"gruppo": "GESTIONALE_CLIENTE", "gestionale_cliente-gestionale.cliente_denominazione": "ABB S.p.A."}
    )
    assert r.status_code == 302 and gestionale.dati_cliente(abb)["denominazione"] == "ABB S.p.A."
