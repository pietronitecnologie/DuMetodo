import datetime as dt
import io
from decimal import Decimal

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from dumetodo.anagrafica.models import Articolo
from dumetodo.comunicazioni.models import EmailInUscita
from dumetodo.configurazione import servizi as config
from dumetodo.core.models import Reparto
from dumetodo.magazzino import formati_ddt, servizi
from dumetodo.magazzino.models import (
    Componente,
    ComponenteDistinta,
    ConteggioInventario,
    DDTIngresso,
    DistintaBase,
    MaterialeIngresso,
    RichiestaMateriale,
)
from dumetodo.ordini.servizi import OperazioneNonConsentita


@pytest.fixture
def pulsanti(db):
    return Reparto.objects.create(codice="GK01-PLS", nome="Pulsanti")


@pytest.fixture
def elettronica(db):
    return Reparto.objects.create(codice="GK01-ETT", nome="Elettronica")


@pytest.fixture
def email_magazzino(db):
    config.scrivi_molti(
        {
            "email.mittente": "metodo@example.com",
            "email.magazzino_cliente": "mag1@cliente.example.com, mag2@cliente.example.com",
            "email.magazzino_fornitore": "magazzino@fornitore.example.com",
        }
    )
    config.invalida_cache()


def _xlsx(contenuto):
    from openpyxl import load_workbook

    return [list(r) for r in load_workbook(io.BytesIO(contenuto)).active.iter_rows(values_only=True)]


# ----------------------------------------------------------------------------------------- componenti
def test_componente_normalizzato_e_unico_con_variante_vuota(operatore):
    c = servizi.crea_componente(operatore, "  abc-1 ", "", descrizione="Vite")
    assert (c.codice, c.variante, str(c)) == ("ABC-1", "", "ABC-1")
    with pytest.raises(OperazioneNonConsentita, match="esiste già"):
        servizi.crea_componente(operatore, "ABC-1", None, descrizione="")
    # Il vincolo è nel database (in Metodo (X, NULL) poteva esistere più volte).
    with pytest.raises(IntegrityError), transaction.atomic():
        Componente.objects.bulk_create([Componente(codice="ABC-1", variante="")])
    assert servizi.crea_componente(operatore, "abc-1", "r").codice_completo == "ABC-1.R"


def test_codice_non_normalizzato_rifiutato_dal_database(db):
    Componente.objects.create(codice="X1")
    with pytest.raises(IntegrityError), transaction.atomic():
        Componente.objects.filter(codice="X1").update(codice="x1 ")


def test_crea_non_sovrascrive_e_modifica_con_attivo(operatore):
    c = servizi.crea_componente(operatore, "C1", descrizione="Originale", fornitore="ACME", prezzo_medio="1,50")
    assert c.prezzo_medio == Decimal("1.5")
    c = servizi.modifica_componente(
        operatore, c.pk, descrizione="Nuova", um="M", prezzo_medio="", fornitore="ACME", note="", attivo=False
    )
    c.refresh_from_db()
    assert (c.descrizione, c.um, c.prezzo_medio, c.attivo) == ("Nuova", "M", None, False)
    assert c.history.count() == 2  # storicizzato
    with pytest.raises(OperazioneNonConsentita, match="Unità"):
        servizi.modifica_componente(operatore, c.pk, um="KG")


def test_cliente_non_gestisce_anagrafiche(cliente):
    with pytest.raises(OperazioneNonConsentita):
        servizi.crea_componente(cliente, "C1")


def test_assicura_componente_non_confonde_le_varianti(db):
    a = Componente.objects.create(codice="X", variante="A")
    Componente.objects.create(codice="X", variante="B")
    # In Metodo get_or_create(codice=X) senza variante andava in MultipleObjectsReturned.
    base = servizi.assicura_componente("x", "", descrizione="Base")
    assert base.variante == "" and base.pk != a.pk
    assert servizi.assicura_componente("X", "a") == a


def test_pagine_componenti(client, operatore):
    client.force_login(operatore)
    r = client.post("/magazzino/componenti/nuovo/", {"codice": "k-9", "descrizione": "<b>grassetto</b>", "um": "EA"})
    assert r.status_code == 302
    c = Componente.objects.get(codice="K-9")
    r = client.get("/magazzino/componenti/?q=k-9")
    assert b"K-9" in r.content and b"&lt;b&gt;grassetto" in r.content
    r = client.get("/magazzino/componenti/suggerimenti/?codice=k-")
    assert b'<option value="K-9">' in r.content
    r = client.post(
        f"/magazzino/componenti/{c.pk}/",
        {"descrizione": "x", "um": "EA", "torna": "https://evil.example.com/"},
    )
    assert r.status_code == 302 and r["Location"] == "/magazzino/componenti/"
    r = client.get(f"/magazzino/componenti/{c.pk}/?torna=javascript:alert(1)")
    assert b"javascript:" not in r.content
    c.refresh_from_db()
    assert not c.attivo  # checkbox non spuntata


# ----------------------------------------------------------------------------------------- articoli finiti
def test_articoli_finiti_modifica_e_nuovo(client, operatore, pulsanti):
    a = Articolo.objects.create(codice="ART-1", descrizione="vecchia")
    Articolo.objects.create(codice="ART-2", nuovo=False)
    client.force_login(operatore)
    r = client.get("/magazzino/articoli/?nuovo=1")
    assert b"ART-1" in r.content and b"ART-2" not in r.content
    r = client.post(
        f"/magazzino/articoli/{a.pk}/modifica/",
        {
            "descrizione": "Pulsante",
            "reparto": pulsanti.pk,
            "um": "EA",
            "tabulato": "2",
            "torna": "/magazzino/articoli/?q=ART",
        },
    )
    assert r["Location"] == "/magazzino/articoli/?q=ART"
    a.refresh_from_db()
    assert (a.descrizione, a.reparto, a.tabulato) == ("Pulsante", pulsanti, 2)
    # Valore esplicito: ripetere il POST (F5) non inverte di nuovo come in Metodo.
    for _ in range(2):
        client.post(f"/magazzino/articoli/{a.pk}/nuovo/", {"nuovo": "0"})
    a.refresh_from_db()
    assert a.nuovo is False
    assert client.get(f"/magazzino/articoli/{a.pk}/nuovo/").status_code == 405
    assert client.get(f"/magazzino/articoli/?reparto={pulsanti.pk}&um=EA&tabulato=2").status_code == 200


def test_articolo_tabulato_non_valido(operatore):
    a = Articolo.objects.create(codice="ART-1")
    with pytest.raises(OperazioneNonConsentita, match="Tabulato"):
        servizi.modifica_articolo(operatore, a.pk, descrizione="", reparto_id=None, um="EA", tabulato=9)


# ----------------------------------------------------------------------------------------- inventario
def test_conteggio_valida_e_crea_componente(operatore):
    c = servizi.registra_conteggio(operatore, "abc", "", "12,5")
    assert c.qta == Decimal("12.500") and c.utente == operatore and timezone.is_aware(c.data)
    assert c.componente.codice == "ABC" and "inventario" in c.componente.descrizione
    for qta in ("", "abc", "-1", "1,2345"):
        with pytest.raises(OperazioneNonConsentita):
            servizi.registra_conteggio(operatore, "abc", "", qta)
    with pytest.raises(OperazioneNonConsentita, match="Codice"):
        servizi.registra_conteggio(operatore, "", "", "1")
    assert ConteggioInventario.objects.count() == 1 and Componente.objects.count() == 1


def test_giacenza_e_ultimo_conteggio_non_somma(operatore):
    servizi.registra_conteggio(operatore, "A", "", "10")
    ultimo = servizi.registra_conteggio(operatore, "A", "", "7")
    servizi.registra_conteggio(operatore, "B", "1", "3")
    righe = list(servizi.ultimi_conteggi())
    assert [(r.componente.codice_completo, r.qta) for r in righe] == [("A", 7), ("B.1", 3)]
    assert righe[0].pk == ultimo.pk
    assert [r.componente.codice for r in servizi.ultimi_conteggi("b")] == ["B"]
    _, contenuto = servizi.xlsx_inventario("ultimi")
    assert _xlsx(contenuto)[1][:4] == ["A", None, "Aggiunto da inventario il " + f"{timezone.localdate():%d/%m/%Y}", 7]
    _, contenuto = servizi.xlsx_inventario("somma")
    assert _xlsx(contenuto)[1][3] == 17
    vecchio = ConteggioInventario.objects.create(componente=ultimo.componente, qta=Decimal("1.5"))
    ConteggioInventario.objects.filter(pk=vecchio.pk).update(data=timezone.now().replace(year=2020))
    nome, contenuto = servizi.xlsx_inventario("tutti", 2020)
    righe_xlsx = _xlsx(contenuto)
    assert nome == "Inventario_2020.xlsx" and len(righe_xlsx) == 2 and righe_xlsx[1][3] == 1.5  # decimali conservati
    assert 2020 in servizi.anni_inventario()


def test_pagine_inventario(client, operatore):
    client.force_login(operatore)
    assert client.get("/magazzino/inventario/aggiungi/").status_code == 405  # Metodo: la GET creava record
    r = client.post("/magazzino/inventario/aggiungi/", {"codice": "Z1", "variante": "", "qta": "4"})
    assert r.status_code == 302 and ConteggioInventario.objects.count() == 1
    r = client.post("/magazzino/inventario/aggiungi/", {"codice": "Z1", "qta": "tanti"}, follow=True)
    assert "numero non valido" in r.content.decode()
    for url in ("/magazzino/inventario/", "/magazzino/inventario/?vista=storico&anno=abc&q=z"):
        assert client.get(url).status_code == 200
    r = client.get("/magazzino/inventario/esporta/?tipo=tutti&anno=xyz")  # Metodo: errore 500
    assert r.status_code == 200 and r["Content-Disposition"] == 'attachment; filename="Inventario.xlsx"'


# ----------------------------------------------------------------------------------------- richieste materiali
def test_richiesta_ripetuta_incrementa_senza_doppioni(operatore, pulsanti, elettronica):
    r, creata = servizi.richiedi_materiale(operatore, pulsanti, " ab1 ", "x", urgente=True)
    assert creata and (r.codice, r.variante, r.numero_richieste) == ("AB1", "X", 1)
    r2, creata = servizi.richiedi_materiale(operatore, pulsanti, "AB1", "X", urgente=False)
    assert not creata and r2.pk == r.pk and r2.numero_richieste == 2 and r2.urgente
    servizi.richiedi_materiale(operatore, elettronica, "AB1", "X")
    assert RichiestaMateriale.objects.count() == 2


def test_una_sola_richiesta_aperta_vincolo_db(operatore, pulsanti, amministratore):
    r, _ = servizi.richiedi_materiale(operatore, pulsanti, "AB1")
    with pytest.raises(IntegrityError), transaction.atomic():
        RichiestaMateriale.objects.create(codice="AB1", variante="", reparto=pulsanti)
    servizi.ricevi_richiesta(amministratore, r.pk)
    r.refresh_from_db()
    assert r.ricevuta and r.ricevuta_il and r.ricevuta_da == amministratore  # Metodo non salvava la data
    nuova, creata = servizi.richiedi_materiale(operatore, pulsanti, "AB1")
    assert creata and nuova.pk != r.pk
    with pytest.raises(OperazioneNonConsentita, match="già ricevuto"):
        servizi.ricevi_richiesta(amministratore, r.pk)


def test_azioni_amministrative_controllate_lato_server(operatore, amministratore, pulsanti, email_magazzino):
    r, _ = servizi.richiedi_materiale(operatore, pulsanti, "AB1", urgente=True)
    for azione in (
        lambda: servizi.ricevi_richiesta(operatore, r.pk),
        lambda: servizi.elimina_richiesta(operatore, r.pk),
        lambda: servizi.imposta_urgenza(operatore, r.pk, False),
        lambda: servizi.invia_email_richieste(operatore, pulsanti, "MANCANTE", [r.pk]),
    ):
        with pytest.raises(OperazioneNonConsentita, match="permessi"):
            azione()
    assert servizi.imposta_urgenza(amministratore, r.pk, False).urgente is False  # in Metodo non si poteva
    servizi.elimina_richiesta(amministratore, r.pk)
    assert not RichiestaMateriale.objects.exists()


def test_email_richieste_accodata_con_flag_e_escape(
    amministratore, operatore, pulsanti, email_magazzino, django_capture_on_commit_callbacks
):
    a, _ = servizi.richiedi_materiale(operatore, pulsanti, "<img src=x onerror=alert(1)>")
    b, _ = servizi.richiedi_materiale(operatore, pulsanti, "B2", "V")
    with django_capture_on_commit_callbacks(execute=True):
        m = servizi.invia_email_richieste(amministratore, pulsanti, "URGENTE", [a.pk, b.pk])
    assert m.oggetto == "Richiesta Materiale - Urgente"
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.mail_urgente and b.mail_urgente and not a.mail_mancante and a.ultima_email == b.ultima_email
    inviata = mail.outbox[0]
    assert inviata.to == ["mag1@cliente.example.com", "mag2@cliente.example.com"]
    assert inviata.reply_to == ["magazzino@fornitore.example.com"] and inviata.from_email == "metodo@example.com"
    assert "B2.V - Materiale Urgente per commessa." in inviata.body
    html = inviata.alternatives[0][0]
    assert "<IMG" not in html and "&lt;IMG SRC=X ONERROR=ALERT(1)&gt;" in html
    assert EmailInUscita.objects.get().riferimento == "richieste:GK01-PLS:URGENTE"


def test_email_senza_destinatari_non_segna_nulla(amministratore, operatore, pulsanti):
    r, _ = servizi.richiedi_materiale(operatore, pulsanti, "AB1")
    with pytest.raises(OperazioneNonConsentita, match="destinatario"):
        servizi.invia_email_richieste(amministratore, pulsanti, "MANCANTE", [r.pk])
    r.refresh_from_db()
    assert not r.mail_mancante and not EmailInUscita.objects.exists()


def test_email_selezione_limitata_al_reparto_e_alle_aperte(
    amministratore, operatore, pulsanti, elettronica, email_magazzino
):
    altro, _ = servizi.richiedi_materiale(operatore, elettronica, "E1")
    with pytest.raises(OperazioneNonConsentita, match="non sono più aperte"):
        servizi.invia_email_richieste(amministratore, pulsanti, "MANCANTE", [altro.pk])
    with pytest.raises(OperazioneNonConsentita, match="Nessuna richiesta"):
        servizi.invia_email_richieste(amministratore, pulsanti, "MANCANTE", [])
    with pytest.raises(OperazioneNonConsentita, match="Tipo"):
        servizi.componi_email(pulsanti, "BOH", [1])
    # "tutte": solo quelle mai comunicate del reparto; con zero richieste non parte un'email vuota.
    with pytest.raises(OperazioneNonConsentita, match="Nessuna richiesta"):
        servizi.invia_email_richieste(amministratore, pulsanti, "MANCANTE", tutte_da_comunicare=True)
    p1, _ = servizi.richiedi_materiale(operatore, pulsanti, "P1")
    p2, _ = servizi.richiedi_materiale(operatore, pulsanti, "P2")
    servizi.invia_email_richieste(amministratore, pulsanti, "MANCANTE", [p1.pk])
    m = servizi.invia_email_richieste(amministratore, pulsanti, "SOLLECITO", tutte_da_comunicare=True)
    assert [r.pk for r in m.richieste] == [p2.pk]


def test_pagine_richieste(client, operatore, amministratore, pulsanti, email_magazzino):
    client.force_login(operatore)
    r = client.get("/magazzino/richieste/")
    assert r.status_code == 200 and b"Pulsanti" in r.content
    r = client.post("/magazzino/richieste/reparto/GK01-PLS/aggiungi/", {"codice": "ab1", "urgente": "on"}, follow=True)
    assert r.status_code == 200 and b"AB1" in r.content and b"Non in anagrafica" in r.content
    richiesta = RichiestaMateriale.objects.get()
    assert client.get("/magazzino/richieste/reparto/NON-ESISTE/").status_code == 404  # Metodo: 500
    assert client.post(f"/magazzino/richieste/{richiesta.pk}/ricevi/").status_code == 403
    assert (
        client.get(f"/magazzino/richieste/reparto/GK01-PLS/email/?tipo=MANCANTE&id={richiesta.pk}").status_code == 403
    )
    r = client.get("/magazzino/richieste/reparto/GK01-PLS/esporta/")
    assert _xlsx(r.content)[1][:3] == ["AB1", None, "No"]

    client.force_login(amministratore)
    r = client.get(f"/magazzino/richieste/reparto/GK01-PLS/email/?tipo=MANCANTE&id={richiesta.pk}")
    testo = r.content.decode()
    assert "mag1@cliente.example.com, mag2@cliente.example.com" in testo  # destinatari
    assert "magazzino@fornitore.example.com" in testo.split("Rispondi a")[1]  # solo rispondi-a
    richiesta.refresh_from_db()
    assert not richiesta.mail_mancante  # l'anteprima non modifica nulla
    assert client.get("/magazzino/richieste/reparto/GK01-PLS/email/invia/").status_code == 405
    r = client.post("/magazzino/richieste/reparto/GK01-PLS/email/invia/", {"tipo": "MANCANTE", "id": richiesta.pk})
    assert r.status_code == 302
    richiesta.refresh_from_db()
    assert richiesta.mail_mancante
    client.post(f"/magazzino/richieste/{richiesta.pk}/ricevi/", {"torna": "/magazzino/richieste/reparto/GK01-PLS/"})
    richiesta.refresh_from_db()
    assert richiesta.ricevuta


# ----------------------------------------------------------------------------------------- distinte base
def test_distinta_collegata_all_articolo_e_riutilizzabile_dopo_eliminazione(amministratore):
    Articolo.objects.create(codice="Pls-1")
    d = servizi.crea_distinta(amministratore, "PLS-1", "", "Pulsantiera")
    assert d.articolo.codice == "Pls-1"  # trovato senza distinguere maiuscole
    with pytest.raises(OperazioneNonConsentita, match="Esiste già"):
        servizi.crea_distinta(amministratore, "pls-1")
    servizi.elimina_distinta(amministratore, d.pk)
    d.refresh_from_db()
    assert d.eliminata_il and d.eliminata_da == amministratore
    d2 = servizi.crea_distinta(amministratore, "PLS-1")  # in Metodo il codice restava bloccato per sempre
    assert d2.pk != d.pk
    nuovo = servizi.crea_distinta(amministratore, "nuovo-art", "b")
    assert nuovo.articolo.codice == "NUOVO-ART" and nuovo.variante == "B"


def test_componenti_della_distinta(amministratore):
    d = servizi.crea_distinta(amministratore, "PADRE")
    a = servizi.aggiungi_componente_distinta(amministratore, d.pk, "vite", "", "4", "EA", "")
    b = servizi.aggiungi_componente_distinta(amministratore, d.pk, "CAVO", "R", "0,25", "M", "rosso")
    assert (a.posizione, b.posizione, b.quantita, b.componente.um) == (1, 2, Decimal("0.25"), "M")
    with pytest.raises(OperazioneNonConsentita, match="già nella distinta"):
        servizi.aggiungi_componente_distinta(amministratore, d.pk, "VITE", "", "1")
    for qta in ("0", "x", ""):
        with pytest.raises(OperazioneNonConsentita):
            servizi.aggiungi_componente_distinta(amministratore, d.pk, "ALTRO", "", qta)
    assert not Componente.objects.filter(codice="ALTRO").exists()  # transazione annullata
    servizi.sposta_componente_distinta(amministratore, b.pk, su=True)
    assert list(d.componenti.order_by("posizione").values_list("componente__codice", flat=True)) == ["CAVO", "VITE"]
    servizi.modifica_componente_distinta(amministratore, a.pk, "6", "EA", "inox")
    a.refresh_from_db()
    assert (a.quantita, a.note) == (6, "inox")
    with pytest.raises(IntegrityError), transaction.atomic():
        ComponenteDistinta.objects.filter(pk=a.pk).update(quantita=0)
    servizi.elimina_componente_distinta(amministratore, a.pk)
    assert d.componenti.count() == 1
    nome, contenuto = servizi.xlsx_distinte()
    assert nome.startswith("distinte_base_")
    assert _xlsx(contenuto)[1] == ["PADRE", None, None, "CAVO", "R", 0.25, "Metri", "rosso"]


def test_distinte_solo_amministratore(client, operatore, amministratore):
    with pytest.raises(OperazioneNonConsentita):
        servizi.crea_distinta(operatore, "X")
    d = servizi.crea_distinta(amministratore, "A'B\"<C>")
    client.force_login(operatore)
    assert client.post("/magazzino/distinte/nuova/", {"articolo": "Y"}).status_code == 403
    assert client.post(f"/magazzino/distinte/{d.pk}/elimina/").status_code == 403
    assert client.post(f"/magazzino/distinte/{d.pk}/componenti/", {"codice": "Z", "quantita": "1"}).status_code == 403
    r = client.get(f"/magazzino/distinte/{d.pk}/")
    assert r.status_code == 200 and "A&#x27;B&quot;&lt;C&gt;" in r.content.decode()
    assert client.get("/magazzino/distinte/?q=A").status_code == 200
    client.force_login(amministratore)
    r = client.post(f"/magazzino/distinte/{d.pk}/componenti/", {"codice": "z", "quantita": "2", "um": "EA"})
    assert r.status_code == 302 and d.componenti.get().componente.codice == "Z"
    riga = d.componenti.get()
    client.post(f"/magazzino/distinte/componenti/{riga.pk}/modifica/", {"quantita": "3", "um": "EA"})
    riga.refresh_from_db()
    assert riga.quantita == 3
    assert client.get(f"/magazzino/distinte/componenti/{riga.pk}/elimina/").status_code == 405
    client.post(f"/magazzino/distinte/{d.pk}/elimina/")
    assert client.get(f"/magazzino/distinte/{d.pk}/").status_code == 404
    assert DistintaBase.objects.filter(pk=d.pk).exists()  # eliminazione logica


# ----------------------------------------------------------------------------------------- DDT: parser
CSV = (
    '"Numero documento e appendice";"Data";"Ragione sociale";"Codice articolo";"Descrizione articolo/conto";'
    '"Unita\' di misura";"Quantita\'"\r\n'
    '"33";09/01/2026;"FORNITORE S.R.L.";"18140075br";"Capsula Braille Perché";"NR";10,000\r\n'
    '"33";09/01/2026;"FORNITORE S.R.L.";"18140075BR";"Capsula Braille";"NR";1.000,5\r\n'
    '"33";09/01/2026;"FORNITORE S.R.L.";"";"Riga senza codice";"NR";1\r\n'
    '"34";31/02/2026;"FORNITORE S.R.L.";"X";"";"NR";1\r\n'
    '"35";10/01/2026;"ALTRO";"Y";"";"MT";0\r\n'
    '"35";10/01/2026;"ALTRO";"Y";"";"MT";-2\r\n'
    '"35";10/01/2026;"ALTRO";"Y";"";"MT";2,5\r\n'
)


def test_csv_utf8_con_bom_e_cp1252():
    for contenuto in ("﻿" + CSV).encode("utf-8"), CSV.encode("cp1252"):
        lettura = formati_ddt.leggi_csv(contenuto)
        assert [(r.numero, r.codice, r.quantita, r.riga_documento) for r in lettura.righe] == [
            ("33", "18140075BR", Decimal("10.000"), 1),
            ("33", "18140075BR", Decimal("1000.5"), 2),  # stesso codice due volte: entrambe le righe
            ("35", "Y", Decimal("2.5"), 3),
        ]
        assert lettura.righe[0].descrizione == "Capsula Braille Perché"
        assert lettura.righe[0].data == dt.date(2026, 1, 9) and lettura.righe[0].fornitore == "FORNITORE S.R.L."
        assert len(lettura.scarti) == 4
        assert any("data '31/02/2026' non valida" in s for s in lettura.scarti)
        assert any("zero o negativa" in s for s in lettura.scarti)


def test_csv_intestazione_errata():
    with pytest.raises(formati_ddt.ErroreFormato, match="Intestazione"):
        formati_ddt.leggi_csv(b"a;b;c\n1;2;3\n")
    with pytest.raises(formati_ddt.ErroreFormato, match="vuoto"):
        formati_ddt.leggi_csv(b"")


XML = """<?xml version="1.0" encoding="windows-1252"?>
<Docs>
  <Doc>
    <DocType>5</DocType><DocNumber>5</DocNumber><DocDate>2026-01-07</DocDate>
    <Anag><Name>Fornitore Città S.r.l.</Name><VatNumber>IT000</VatNumber></Anag>
    <DocRows>
      <DocRow><RowType>1</RowType><Product><Code>abc123</Code></Product><Description>Vite</Description>
        <Quantity>100</Quantity><Price>5.50</Price></DocRow>
      <DocRow><RowType>2</RowType><Description>Riga di commento</Description></DocRow>
      <DocRow><Product><Code>ABC123</Code></Product><Quantity>2.5</Quantity><Price>5,50</Price></DocRow>
      <DocRow><Product><Code>Z</Code></Product><Quantity>0</Quantity></DocRow>
    </DocRows>
  </Doc>
  <Doc><DocType>9</DocType><DocNumber>F1</DocNumber><DocDate>2026-01-08</DocDate>
    <DocRows><DocRow><Product><Code>Q</Code></Product><Quantity>1</Quantity></DocRow></DocRows></Doc>
  <Doc><DocType>5</DocType><DocDate>2026-01-08</DocDate><DocRows/></Doc>
</Docs>"""


def test_xml_gestionale_e_filtro_tipo_documento():
    lettura = formati_ddt.leggi_xml(XML.encode("cp1252"))
    righe = [(r.numero, r.codice, r.quantita, r.prezzo_unitario, r.riga_documento) for r in lettura.righe]
    assert righe == [
        ("5", "ABC123", Decimal(100), Decimal("5.5000"), 1),
        ("5", "ABC123", Decimal("2.5"), Decimal("5.5000"), 3),
        ("F1", "Q", Decimal(1), None, 1),
    ]
    assert lettura.righe[0].fornitore == "Fornitore Città S.r.l." and lettura.righe[0].data == dt.date(2026, 1, 7)
    assert len(lettura.scarti) == 3  # commento, quantità zero, documento senza numero
    solo_ddt = formati_ddt.leggi_xml(XML.encode("cp1252"), ["5"])
    assert {r.numero for r in solo_ddt.righe} == {"5"}
    assert any("tipo documento 9 non accettato" in s for s in solo_ddt.scarti)


@pytest.mark.parametrize(
    "contenuto",
    [
        b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><Docs>&b;</Docs>',
        b'<!doctype Docs SYSTEM "file:///etc/passwd"><Docs/>',
        "<Docs/>".encode("utf-16"),
        b"<Altro/>",
        b"<Docs><Doc>",
        b"<Docs></Docs>",
    ],
)
def test_xml_pericoloso_o_non_valido_rifiutato(contenuto):
    with pytest.raises(formati_ddt.ErroreFormato):
        formati_ddt.leggi_xml(contenuto)


# ----------------------------------------------------------------------------------------- DDT: servizi
def test_importazione_csv_atomica_e_reimportazione(operatore):
    Componente.objects.create(codice="18140075BR", variante="A")  # non deve dare MultipleObjectsReturned
    esito = servizi.importa_file_ddt(operatore, "ddt.csv", CSV.encode("cp1252"))
    assert (len(esito.ddt_creati), esito.materiali_importati, len(esito.scarti)) == (2, 3, 4)
    ddt = DDTIngresso.objects.get(numero="33")
    assert ddt.origine == "CSV" and ddt.materiali.count() == 2
    assert not ddt.materiali.filter(verificato=True).exists()  # da verificare, come in Metodo
    comp = Componente.objects.get(codice="18140075BR", variante="")
    assert (comp.descrizione, comp.fornitore, comp.um) == ("Capsula Braille Perché", "FORNITORE S.R.L.", "EA")
    assert Componente.objects.get(codice="Y").um == "M"
    # Reimportare lo stesso file non duplica nulla.
    esito = servizi.importa_file_ddt(operatore, "ddt.csv", CSV.encode("cp1252"))
    assert (len(esito.ddt_aggiornati), esito.materiali_importati, esito.materiali_gia_presenti) == (2, 0, 3)
    assert MaterialeIngresso.objects.count() == 3


def test_importazione_xml_e_limiti(operatore):
    esito = servizi.importa_file_ddt(operatore, "ddt.XML", XML.encode("cp1252"))
    assert esito.materiali_importati == 3 and all(m.verificato for m in MaterialeIngresso.objects.all())
    assert MaterialeIngresso.objects.filter(prezzo_unitario=Decimal("5.5")).count() == 2
    config.scrivi("magazzino.dimensione_massima_file_ddt_mb", 1)
    config.invalida_cache()
    with pytest.raises(OperazioneNonConsentita, match="troppo grande"):
        servizi.importa_file_ddt(operatore, "x.csv", b"x" * (1024 * 1024 + 1))
    with pytest.raises(OperazioneNonConsentita, match="solo file"):
        servizi.importa_file_ddt(operatore, "x.pdf", b"%PDF")
    with pytest.raises(OperazioneNonConsentita, match="DTD"):
        servizi.importa_file_ddt(operatore, "x.xml", b'<!DOCTYPE x [<!ENTITY a "b">]><Docs/>')


def test_inserimento_manuale_tutto_o_niente(operatore, amministratore):
    ddt = servizi.crea_ddt(operatore, "12/A", dt.date(2026, 3, 1), "ACME")
    with pytest.raises(OperazioneNonConsentita, match="già registrato"):
        servizi.crea_ddt(operatore, "12/A", dt.date(2026, 3, 1), "ACME")
    with pytest.raises(OperazioneNonConsentita, match="obbligatorio"):
        servizi.crea_ddt(operatore, "", dt.date(2026, 3, 1), "ACME")
    with pytest.raises(OperazioneNonConsentita):
        servizi.aggiungi_materiale(operatore, ddt.pk, codice="NUOVO", qta="zero")
    assert not Componente.objects.filter(codice="NUOVO").exists() and not ddt.materiali.exists()
    m = servizi.aggiungi_materiale(
        operatore, ddt.pk, codice="nuovo", qta="3", prezzo_unitario="2,5", descrizione="Dado", unita_misura="pz"
    )
    assert (m.componente.codice, m.quantita, m.prezzo_unitario, m.unita_misura, m.verificato) == (
        "NUOVO",
        3,
        Decimal("2.5"),
        "PZ",
        True,
    )
    assert m.componente.prezzo_medio == Decimal("2.5") and m.componente.fornitore == "ACME"
    assert servizi.imposta_verificato(operatore, ddt.pk, None, False) == 1
    with pytest.raises(OperazioneNonConsentita):
        servizi.elimina_materiale(operatore, m.pk)
    servizi.elimina_materiale(amministratore, m.pk)
    assert not ddt.materiali.exists()


def test_richieste_chiuse_dal_ddt_e_le_altre_restano_aperte(operatore, pulsanti):
    servizi.richiedi_materiale(operatore, pulsanti, "ABC123")
    servizi.richiedi_materiale(operatore, pulsanti, "ALTRO")
    servizi.importa_file_ddt(operatore, "d.xml", XML.encode("cp1252"))
    ddt = DDTIngresso.objects.get(numero="5")
    assert not servizi.richieste_aperte_del_ddt(ddt).exists()  # chiuse automaticamente all'arrivo
    assert list(RichiestaMateriale.objects.filter(ricevuta=False).values_list("codice", flat=True)) == ["ALTRO"]


def test_pagine_ddt(client, operatore):
    client.force_login(operatore)
    r = client.post("/magazzino/ddt/nuovo/", {"numero": "77", "data": "2026-02-01", "fornitore": "ACME"})
    ddt = DDTIngresso.objects.get()
    assert r["Location"] == f"/magazzino/ddt/{ddt.pk}/"
    r = client.post(
        f"/magazzino/ddt/{ddt.pk}/materiali/",
        {"codice": "c1", "quantita": "2", "unita_misura": "EA", "data_ingresso": "2026-02-02"},
        follow=True,
    )
    assert b"Materiale inserito" in r.content and b"C1" in r.content
    m = ddt.materiali.get()
    assert m.data_ingresso == dt.date(2026, 2, 2)
    client.post(f"/magazzino/ddt/{ddt.pk}/verifica/", {"id": m.pk, "verificato": "0"})
    m.refresh_from_db()
    assert not m.verificato
    assert client.post(f"/magazzino/ddt/materiali/{m.pk}/elimina/").status_code == 403
    r = client.post("/magazzino/ddt/importa/", {"file": SimpleUploadedFile("d.csv", CSV.encode("utf-8"))})
    assert r.status_code == 200 and b"materiali importati: <strong>3</strong>" in r.content
    r = client.post("/magazzino/ddt/importa/", {"file": SimpleUploadedFile("d.xml", b"<Docs><Doc>")})
    assert b"Nessun dato" in r.content
    assert client.get("/magazzino/ddt/?q=acme").status_code == 200
    r = client.get("/magazzino/ddt/materiali/?codice=c1&da_verificare=1")
    assert b"C1" in r.content
    r = client.get("/magazzino/ddt/materiali/?esporta=1&fornitore=FORNITORE&data_da=2026-01-01&data_a=bad")
    assert _xlsx(r.content)[1][:4] == ["18140075BR", None, "Capsula Braille Perché", 10]


# ----------------------------------------------------------------------------------------- accesso e navigazione
@pytest.mark.parametrize(
    "url",
    [
        "/magazzino/componenti/",
        "/magazzino/articoli/",
        "/magazzino/inventario/",
        "/magazzino/richieste/",
        "/magazzino/distinte/",
        "/magazzino/ddt/",
        "/magazzino/ddt/importa/",
        "/magazzino/ddt/materiali/",
    ],
)
def test_accesso_pagine(client, cliente, operatore, url):
    assert "/accesso/" in client.get(url)["Location"]
    client.force_login(cliente)
    assert client.get(url).status_code == 403
    client.force_login(operatore)
    assert client.get(url).status_code == 200


def test_pagine_amministratore_con_dati(client, amministratore, pulsanti):
    r, _ = servizi.richiedi_materiale(amministratore, pulsanti, "ABC123", urgente=True)
    servizi.importa_file_ddt(amministratore, "d.xml", XML.encode("cp1252"))  # chiude la richiesta ABC123
    servizi.richiedi_materiale(amministratore, pulsanti, "NONARRIVATO", urgente=True)
    d = servizi.crea_distinta(amministratore, "ART-1")
    servizi.aggiungi_componente_distinta(amministratore, d.pk, "ABC123", "", "2")
    servizi.registra_conteggio(amministratore, "ABC123", "", "5")
    ddt = DDTIngresso.objects.get(numero="5")
    client.force_login(amministratore)
    for url in (
        "/magazzino/richieste/",
        "/magazzino/richieste/reparto/GK01-PLS/",
        f"/magazzino/distinte/{d.pk}/",
        f"/magazzino/ddt/{ddt.pk}/",
        "/magazzino/inventario/?vista=storico",
        "/magazzino/componenti/",
        "/magazzino/articoli/",
        "/magazzino/distinte/esporta/",
    ):
        assert client.get(url).status_code == 200, url
    pagina = client.get(f"/magazzino/ddt/{ddt.pk}/").content.decode()
    assert "chiuse dall'arrivo" in pagina and "ABC123" in pagina
    assert "Mail Sollecito" in client.get("/magazzino/richieste/reparto/GK01-PLS/").content.decode()
    r = client.get("/magazzino/richieste/reparto/GK01-PLS/email/?tipo=MANCANTE&tutte=1")
    assert r.status_code == 200 and "NONARRIVATO - Materiale in esaurimento." in r.content.decode()
    riga = d.componenti.get()
    client.post(f"/magazzino/distinte/componenti/{riga.pk}/sposta/", {"direzione": "giu"})
    assert client.post(f"/magazzino/ddt/materiali/{ddt.materiali.first().pk}/elimina/").status_code == 302


def test_menu_magazzino_solo_per_il_fornitore(client, cliente, operatore):
    client.force_login(operatore)
    assert b"Richiesta materiali" in client.get("/ordini/").content
    client.force_login(cliente)
    assert b"Richiesta materiali" not in client.get("/ordini/").content


def test_parametri_magazzino_registrati():
    from dumetodo.configurazione.registro import REGISTRO, Gruppo

    assert REGISTRO["magazzino.reparti_richieste"].gruppo == Gruppo.MAGAZZINO
    assert REGISTRO["magazzino.dimensione_massima_file_ddt_mb"].default == 5


# ----------------------------------------------------------------------------------------- migrazione da Metodo
SCHEMA_MAGAZZINO = """
CREATE TABLE "Prod_codicecomponente" (id int PRIMARY KEY, codice varchar, variante varchar, descrizione text,
  um varchar, prezzo_medio float, fornitore varchar, note text, attivo bool, data_creazione timestamptz,
  data_modifica timestamptz);
CREATE TABLE "Prod_inventario" (id int PRIMARY KEY, componente_id int, articolo varchar, variante varchar, qta float,
  data timestamptz);
CREATE TABLE "Prod_richiestemateriali" (id int PRIMARY KEY, codice varchar, variante varchar, data_richiesta date,
  data_ricezione date, numero_richieste int, mail_inviata_1 bool, mail_inviata_2 bool, mail_inviata_3 bool,
  materiale_ricevuto bool, reparto_id int, materiale_urgente bool);
CREATE TABLE "Prod_ddtingresso" (id int PRIMARY KEY, numero_ddt varchar, data_ddt date, fornitore varchar,
  data_elaborazione timestamptz, foto_originale varchar, elaborato bool, utente_id int);
CREATE TABLE "Prod_materialeingresso" (id int PRIMARY KEY, ddt_id int, codice varchar, variante varchar,
  descrizione varchar, quantita float, unita_misura varchar, prezzo_unitario numeric, data_ingresso date,
  verificato bool, note text);
CREATE TABLE "Prod_distintabase" (id int PRIMARY KEY, codice_padre varchar, variante varchar, descrizione text,
  data_creazione timestamptz, data_modifica timestamptz, utente_creazione_id int, utente_modifica_id int,
  attiva bool, note text);
CREATE TABLE "Prod_componentedistinta" (id int PRIMARY KEY, distinta_id int, componente_id int,
  codice_componente varchar, variante varchar, quantita float, um varchar, posizione int, note text);
"""

DATI_MAGAZZINO = """
INSERT INTO "Prod_codicecomponente" VALUES
  (1,'VITE',null,'Vite M3','EA',0.1,'ACME','',true,'2024-01-01',null),
  (2,'vite ',null,'','EA',null,'','',true,'2024-01-02',null),
  (3,'CAVO','R','Cavo rosso','M',null,'','',false,'2024-01-03',null);
INSERT INTO "Prod_inventario" VALUES (1,1,'VITE',null,10.5,'2024-02-01 10:00+01'),
  (2,null,'NUOVO.X',null,3,'2024-02-02 10:00+01'),(3,2,'vite',null,-1,'2024-02-03 10:00+01');
INSERT INTO "Prod_richiestemateriali" VALUES
  (1,'VITE','','2024-03-01',null,null,true,false,false,false,1,false),
  (2,'vite',null,'2024-03-05',null,2,false,false,true,false,1,true),
  (3,'CAVO','R','2024-03-01',null,1,false,false,false,true,1,false);
INSERT INTO "Prod_ddtingresso" VALUES (1,'33','2024-01-09','FORNITORE',now(),'',true,1),
  (2,'33','2024-01-09','FORNITORE',now(),'',true,1),(3,null,null,null,'2024-01-10 08:00+01','',true,1);
INSERT INTO "Prod_materialeingresso" VALUES (1,1,'VITE',null,'Vite',10,'NR',0.10,'2024-01-09',false,null),
  (2,2,'VITE',null,'Vite',5,'NR',null,'2024-01-09',true,null),(3,3,'DADO',null,'Dado',0,'EA',null,null,true,null);
INSERT INTO "Prod_distintabase" VALUES (1,'ART-A',null,'Vecchia','2024-01-01','2024-01-01',1,1,true,''),
  (2,'ART-A','','Nuova','2024-02-01','2024-02-01',1,1,true,''),
  (3,'ART-Z',null,'Eliminata','2024-01-01','2024-01-05',1,1,false,'');
INSERT INTO "Prod_componentedistinta" VALUES (1,2,1,'VITE',null,2,'EA',1,''),(2,2,null,'VITE',null,3,'EA',2,''),
  (3,2,null,'CAVO','R',0.5,'M',3,''),(4,3,null,'DADO',null,1,'EA',1,'');
"""


@pytest.fixture
def metodo_magazzino_db():
    import psycopg

    from .test_migrazione import BASE, DATI, SCHEMA

    nome = "metodo_legacy_magazzino_test"  # nome proprio: non interferisce con test_migrazione
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {nome} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {nome}")
    with psycopg.connect(f"{BASE}/{nome}", autocommit=True) as c:
        c.execute(SCHEMA + SCHEMA_MAGAZZINO)
        c.execute(DATI + DATI_MAGAZZINO)
    yield f"{BASE}/{nome}"
    with psycopg.connect(f"{BASE}/postgres", autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {nome} WITH (FORCE)")


@pytest.mark.django_db
def test_migrazione_magazzino(metodo_magazzino_db):
    from django.core.management import call_command

    out = io.StringIO()
    call_command("importa_da_metodo", "--db", metodo_magazzino_db, "--applica", stdout=out)
    assert "Migrazione completata" in out.getvalue() and "componenti duplicati" in out.getvalue()
    # (VITE, NULL) doppio in Metodo -> un solo componente, con la descrizione.
    vite = Componente.objects.get(codice="VITE", variante="")
    assert vite.descrizione == "Vite M3" and vite.creato_il.date() == dt.date(2024, 1, 1)
    assert not Componente.objects.get(codice="CAVO", variante="R").attivo
    assert Componente.objects.filter(codice="NUOVO.X").exists()  # creato dal campo legacy
    assert sorted(ConteggioInventario.objects.values_list("qta", flat=True)) == [3, Decimal("10.5")]
    # Richieste aperte doppie fuse (come elimina_duplicati_richieste); la ricevuta ha la sua data.
    aperta = RichiestaMateriale.objects.get(ricevuta=False)
    assert (aperta.numero_richieste, aperta.urgente, aperta.mail_mancante, aperta.mail_sollecito) == (
        3,
        True,
        True,
        True,
    )
    assert RichiestaMateriale.objects.get(ricevuta=True).ricevuta_il is not None
    # DDT doppi uniti; numero vuoto -> METODO-<id>; quantità zero scartata.
    ddt = DDTIngresso.objects.get(numero="33")
    assert ddt.materiali.count() == 2 and ddt.origine == "MET"
    assert DDTIngresso.objects.filter(numero="METODO-3").exists()
    # Distinte: resta attiva la più recente; componenti ripetuti sommati.
    attiva = DistintaBase.objects.attive().get(articolo__codice="ART-A")
    assert attiva.descrizione == "Nuova"
    assert DistintaBase.objects.filter(articolo__codice="ART-A", eliminata_il__isnull=False).count() == 1
    assert dict(attiva.componenti.values_list("componente__codice", "quantita")) == {"VITE": 5, "CAVO": Decimal("0.5")}
    assert DistintaBase.objects.get(articolo__codice="ART-Z").eliminata_il is not None


def test_arrivo_ddt_chiude_le_richieste_senza_toccare_la_giacenza(operatore, pulsanti):
    import datetime as dt

    from dumetodo.core.models import Reparto
    from dumetodo.magazzino.models import ConteggioInventario

    elettronica = Reparto.objects.create(codice="GK01-ETT", nome="Elettronica prova")
    r1, _ = servizi.richiedi_materiale(operatore, pulsanti, "AB1")
    r2, _ = servizi.richiedi_materiale(operatore, elettronica, "AB1")
    altra, _ = servizi.richiedi_materiale(operatore, pulsanti, "ZZ9")
    conteggi = ConteggioInventario.objects.count()
    ddt = servizi.crea_ddt(operatore, "77", dt.date.today(), "Fornitore X")
    m = servizi.aggiungi_materiale(operatore, ddt.pk, codice="ab1", qta="10")
    for r in (r1, r2):
        r.refresh_from_db()
        assert r.ricevuta and r.ricevuta_con == m and r.ricevuta_da == operatore and r.ricevuta_il
    altra.refresh_from_db()
    assert not altra.ricevuta
    assert ConteggioInventario.objects.count() == conteggi  # la giacenza non cambia


def test_importazione_ddt_chiude_le_richieste(operatore, pulsanti):
    r, _ = servizi.richiedi_materiale(operatore, pulsanti, "ABC123")
    esito = servizi.importa_file_ddt(operatore, "d.xml", XML.encode("cp1252"))
    r.refresh_from_db()
    assert r.ricevuta and esito.richieste_chiuse >= 1
