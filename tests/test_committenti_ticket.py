"""Metodo v3: i ticket seguono i clienti committenti dell'ordine."""

import pytest

from metodo.core.utenti import profilo
from metodo.ticket import servizi
from metodo.ticket.models import Ticket

from .test_committenti import _ordine, abb, dmg  # noqa: F401

pytestmark = pytest.mark.django_db


def test_ticket_solo_dei_committenti_visibili(client, dmg, abb, operatore, caporeparto):  # noqa: F811
    o_dmg, o_abb = _ordine(dmg, "0000100"), _ordine(abb, "0000200")
    t_dmg = servizi.apri(caporeparto, titolo="Su DMG", ordine=o_dmg)
    t_abb = servizi.apri(caporeparto, titolo="Su ABB", ordine=o_abb)
    servizi.apri(caporeparto, titolo="Senza ordine")
    profilo(operatore).committenti.set([dmg])
    client.force_login(operatore)
    elenco = client.get("/ticket/").content.decode()
    assert "Su DMG" in elenco and "Senza ordine" in elenco and "Su ABB" not in elenco
    assert client.get(t_dmg.get_absolute_url()).status_code == 200
    assert client.get(t_abb.get_absolute_url()).status_code == 404
    assert client.post(f"/ticket/{t_abb.pk}/azione/", {"azione": "commenta", "testo": "x"}).status_code == 404
    assert client.get(f"/ticket/ordine/{o_abb.pk}/").status_code == 404
    assert client.get(f"/ticket/nuovo/?ordine={o_abb.pk}").status_code == 404
    r = client.post("/ticket/nuovo/", {"titolo": "Furbo", "categoria": "ALT", "priorita": "2", "ordine": o_abb.pk})
    assert r.status_code == 200 and not Ticket.objects.filter(titolo="Furbo").exists()
