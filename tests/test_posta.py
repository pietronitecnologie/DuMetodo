import datetime as dt
from email.message import EmailMessage

import pytest
from django.test import override_settings
from django.utils import timezone

from metodo.importazione import posta
from metodo.importazione.models import EsitoMessaggio, MessaggioEmail, StatoCasella
from metodo.ordini.models import Ordine

from .test_importazione import _zip, file_txt, riga

IMAP = {"HOST": "imap.example.com", "PORT": 993, "USER": "ordini", "PASSWORD": "x", "FOLDER": "INBOX", "SSL": True}


def _mail(message_id: str, allegati: dict[str, bytes]) -> bytes:
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = "dmg@example.com", "Ordine", message_id
    m.set_content("in allegato")
    for nome, dati in allegati.items():
        m.add_attachment(dati, maintype="application", subtype="octet-stream", filename=nome)
    return m.as_bytes()


class FakeIMAP:
    """Casella finta: ogni messaggio ha una data di arrivo (INTERNALDATE). Registra le ricerche fatte."""

    def __init__(self, messaggi: dict[int, bytes], uidvalidity: int = 7, arrivi: dict | None = None):
        self.messaggi, self.uidvalidity = messaggi, uidvalidity
        base = timezone.now() - dt.timedelta(hours=1)
        self.arrivi = arrivi or {}
        for uid in messaggi:
            self.arrivi.setdefault(uid, base + dt.timedelta(minutes=uid))
        self.ricerche: list = []

    def select_folder(self, folder, readonly):
        assert readonly, "la casella va aperta in sola lettura"
        return {b"UIDVALIDITY": self.uidvalidity}

    def search(self, criteri):
        self.ricerche.append(criteri)
        assert criteri[0] == "SINCE", "si cerca per data di arrivo, non per marca «non letto»"
        return [u for u in self.messaggi if self.arrivi[u].date() >= criteri[1]]

    def fetch(self, uids, campi):
        if "BODY.PEEK[]" in campi:  # PEEK: leggere non segna il messaggio come letto
            return {u: {b"BODY[]": self.messaggi[u]} for u in uids}
        return {u: {b"RFC822.SIZE": len(self.messaggi[u]), b"INTERNALDATE": self.arrivi[u]} for u in uids}

    def logout(self):
        pass


def _gia_letta(fino_al):
    return StatoCasella.objects.create(casella="ordini@imap.example.com/INBOX", letto_fino_al=fino_al)


@pytest.mark.django_db(transaction=True)
@override_settings(ORDINI_IMAP=IMAP)
def test_lettura_casella_importa_e_non_ripete():
    zip_ordine = _zip({"OCL_4300_40.TXT": file_txt(riga()), "OCL.pdf": b"%PDF"})
    messaggi = {
        10: _mail("<a@x>", {"OCL_4300_40_0.ZIP": zip_ordine}),
        11: _mail("<b@x>", {}),  # in Metodo veniva riletta a ogni giro
        12: _mail("<a@x>", {"OCL_4300_40_0.ZIP": zip_ordine}),  # stesso Message-ID (es. inoltro)
    }
    fake = FakeIMAP(messaggi)
    _gia_letta(timezone.now() - dt.timedelta(days=1))  # casella già letta ieri: si importano i messaggi di oggi
    esito = posta.leggi_casella(client_factory=lambda: fake)
    assert esito.eseguita and esito.messaggi == 3 and esito.file == 1
    assert Ordine.objects.count() == 1
    esiti = dict(MessaggioEmail.objects.values_list("uid", "esito"))
    assert esiti == {10: EsitoMessaggio.ELABORATO, 11: EsitoMessaggio.SENZA_ALLEGATI, 12: EsitoMessaggio.DUPLICATO}
    assert StatoCasella.objects.get().letto_fino_al == fake.arrivi[12]

    di_nuovo = posta.leggi_casella(client_factory=lambda: fake)
    assert di_nuovo.messaggi == 0


@pytest.mark.django_db
@override_settings(ORDINI_IMAP={**IMAP, "HOST": ""})
def test_casella_non_configurata_non_fa_nulla():
    assert posta.leggi_casella().motivo == "casella IMAP non configurata"


@pytest.mark.django_db(transaction=True)
@override_settings(ORDINI_IMAP=IMAP)
def test_prima_lettura_parte_da_adesso_e_contatore_a_data():
    """Passaggio dal Metodo precedente: la casella contiene ordini vecchi già importati, da non rileggere; e il
    Metodo precedente legge la stessa casella segnando i messaggi come letti: conta solo la data di arrivo."""
    from django.core.management import call_command

    zip_ordine = _zip({"OCL_4300_40.TXT": file_txt(riga())})
    fake = FakeIMAP({10: _mail("<vecchio@x>", {"OCL_4300_40_0.ZIP": zip_ordine})})
    esito = posta.leggi_casella(client_factory=lambda: fake)
    assert esito.messaggi == 0 and "prima lettura" in esito.motivo
    assert Ordine.objects.count() == 0 and StatoCasella.objects.get().letto_fino_al is not None
    # arriva un ordine nuovo (e il Metodo precedente l'ha già segnato come letto: non importa)
    fake.messaggi[11] = _mail("<nuovo@x>", {"OCL_4300_40_0.ZIP": zip_ordine})
    fake.arrivi[11] = timezone.now() + dt.timedelta(seconds=5)
    assert posta.leggi_casella(client_factory=lambda: fake).file == 1
    assert all(r[0] == "SINCE" for r in fake.ricerche)
    # --dal: si rileggono i messaggi da quel giorno; quello già elaborato da Metodo v2 si salta
    call_command("casella_ordini", "--dal", (timezone.localdate() - dt.timedelta(days=2)).isoformat())
    esito = posta.leggi_casella(client_factory=lambda: fake)
    assert esito.messaggi == 1 and MessaggioEmail.objects.count() == 2
    assert StatoCasella.objects.get().letto_fino_al == fake.arrivi[11]
    prima = timezone.now()
    call_command("casella_ordini", "--da-ora")
    assert StatoCasella.objects.get().letto_fino_al >= prima
    call_command("casella_ordini")  # stato: non deve fallire
