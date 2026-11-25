import smtplib
from unittest import mock

import pytest
from django.core import mail

from metodo.comunicazioni import email as outbox
from metodo.comunicazioni import servizi
from metodo.comunicazioni.models import EmailInUscita, Lato, Nota, StatoEmail, Visibilita
from metodo.ordini.models import Ordine


def test_nota_fornitore_avvisa_il_cliente(ordine, operatore, configura_email, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        nota = servizi.aggiungi_nota(ordine, operatore, "Consegna <b>anticipata</b>")
    assert nota.lato == Lato.FORNITORE
    assert len(mail.outbox) == 1
    m = mail.outbox[0]
    assert m.to == ["cliente1@example.com", "cliente2@example.com"] and m.reply_to == ["fornitore@example.com"]
    html = m.alternatives[0][0]
    assert "&lt;b&gt;anticipata&lt;/b&gt;" in html  # niente HTML iniettato nelle email
    assert EmailInUscita.objects.get().stato == StatoEmail.INVIATA


def test_nota_cliente_avvisa_fornitore(ordine, cliente, configura_email, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        nota = servizi.aggiungi_nota(ordine, cliente, "Quando arriva?", visibilita=Visibilita.INTERNA)
    assert nota.lato == Lato.CLIENTE and nota.visibilita == Visibilita.CONDIVISA  # il cliente non scrive note interne
    assert mail.outbox[0].to == ["fornitore@example.com"]


def test_nota_interna_non_inviata_ne_visibile_al_cliente(ordine, operatore, cliente, configura_email):
    servizi.aggiungi_nota(ordine, operatore, "Solo per noi", visibilita=Visibilita.INTERNA)
    assert not EmailInUscita.objects.exists()
    assert servizi.note_visibili(ordine, cliente).count() == 0
    assert servizi.note_visibili(ordine, operatore).count() == 1


def test_smtp_in_errore_non_perde_la_nota(ordine, operatore, configura_email, django_capture_on_commit_callbacks):
    guasto = mock.patch(
        "django.core.mail.EmailMultiAlternatives.send", side_effect=smtplib.SMTPServerDisconnected("giù")
    )
    with guasto, django_capture_on_commit_callbacks(execute=True):
        servizi.aggiungi_nota(ordine, operatore, "Importante")
    assert Nota.objects.count() == 1
    e = EmailInUscita.objects.get()
    assert e.stato == StatoEmail.ERRORE and e.tentativi == 1 and "giù" in e.ultimo_errore
    EmailInUscita.objects.update(prossimo_tentativo_il=e.creata_il)
    assert outbox.da_ritentare() == [e.pk]
    assert outbox.invia(e.pk) is True
    assert outbox.invia(e.pk) is False  # già inviata: nessun doppio invio
    assert len(mail.outbox) == 1


@pytest.mark.parametrize("testo", ["", "   ", "x" * 5001])
def test_nota_non_valida(ordine, operatore, testo):
    with pytest.raises(servizi.NotaNonValida):
        servizi.aggiungi_nota(ordine, operatore, testo)


def test_elimina_solo_autore_o_admin(ordine, operatore, cliente, amministratore):
    nota = servizi.aggiungi_nota(ordine, cliente, "ciao")
    with pytest.raises(servizi.NotaNonValida):
        servizi.elimina_nota(nota, operatore)
    servizi.elimina_nota(nota, amministratore)
    nota.refresh_from_db()
    assert nota.eliminata and nota.eliminata_da == amministratore  # resta nel DB per l'audit
    assert servizi.note_visibili(ordine, cliente).count() == 0


def test_note_non_lette(ordine, operatore, cliente):
    servizi.aggiungi_nota(ordine, cliente, "prima")
    servizi.aggiungi_nota(ordine, cliente, "seconda")
    servizi.aggiungi_nota(ordine, operatore, "interna", visibilita=Visibilita.INTERNA)
    qs = Ordine.objects.filter(pk=ordine.pk)
    assert servizi.annota_non_lette(qs, operatore).get().note_non_lette == 2
    assert servizi.annota_non_lette(qs, cliente).get().note_non_lette == 0  # le sue + l'interna non visibile
    servizi.segna_lette(ordine, operatore)
    assert servizi.annota_non_lette(qs, operatore).get().note_non_lette == 0


def test_viste_note(client, ordine, cliente, operatore):
    client.force_login(cliente)
    r = client.post(f"/note/ordine/{ordine.pk}/aggiungi/", {"testo": "Ciao Fornitore"})
    assert r.status_code == 200 and b"Ciao Fornitore" in r.content
    nota_interna = servizi.aggiungi_nota(ordine, operatore, "segreto", visibilita=Visibilita.INTERNA)
    assert b"segreto" not in client.get(f"/note/ordine/{ordine.pk}/").content
    assert client.post(f"/note/{nota_interna.pk}/elimina/").status_code == 404
    assert client.get(f"/note/{nota_interna.pk}/elimina/").status_code == 405  # niente modifiche via GET


def test_nomi_azienda_configurabili(
    client, ordine, operatore, cliente, configura_email, django_capture_on_commit_callbacks
):
    from metodo.configurazione import servizi as config

    config.scrivi_molti({"azienda.nome_fornitore": "Geko Srl", "azienda.nome_cliente": "DMG Spa"})
    config.invalida_cache()
    with django_capture_on_commit_callbacks(execute=True):
        servizi.aggiungi_nota(ordine, operatore, "Pronto")
    assert "(Geko Srl)" in mail.outbox[0].body
    client.force_login(cliente)
    html = client.get(f"/ordini/{ordine.pk}/").content.decode()
    assert "comunicazione con Geko Srl" in html and "Operatore</strong> · Geko Srl" in html
