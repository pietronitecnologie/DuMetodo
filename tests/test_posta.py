from email.message import EmailMessage

import pytest
from django.test import override_settings

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
    def __init__(self, messaggi: dict[int, bytes], uidvalidity: int = 7):
        self.messaggi, self.uidvalidity = messaggi, uidvalidity

    def select_folder(self, folder, readonly):
        assert readonly, "la casella va aperta in sola lettura"
        return {b"UIDVALIDITY": self.uidvalidity}

    def search(self, criteri):
        minimo = int(criteri[1].split(":")[0])
        uids = [u for u in self.messaggi if u >= minimo]
        return uids or [max(self.messaggi)]  # come IMAP reale: "N:*" restituisce sempre l'ultimo

    def fetch(self, uids, campi):
        uid = uids[0]
        if "BODY.PEEK[]" in campi:
            return {uid: {b"BODY[]": self.messaggi[uid]}}
        return {uid: {b"RFC822.SIZE": len(self.messaggi[uid]), b"INTERNALDATE": None}}

    def logout(self):
        pass


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
    esito = posta.leggi_casella(client_factory=lambda: fake)
    assert esito.eseguita and esito.messaggi == 3 and esito.file == 1
    assert Ordine.objects.count() == 1
    esiti = dict(MessaggioEmail.objects.values_list("uid", "esito"))
    assert esiti == {10: EsitoMessaggio.ELABORATO, 11: EsitoMessaggio.SENZA_ALLEGATI, 12: EsitoMessaggio.DUPLICATO}
    assert StatoCasella.objects.get().ultimo_uid == 12

    di_nuovo = posta.leggi_casella(client_factory=lambda: fake)
    assert di_nuovo.messaggi == 0


@pytest.mark.django_db
@override_settings(ORDINI_IMAP={**IMAP, "HOST": ""})
def test_casella_non_configurata_non_fa_nulla():
    assert posta.leggi_casella().motivo == "casella IMAP non configurata"
