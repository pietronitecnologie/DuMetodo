# ruff: noqa: F811
"""Ticket: apertura dall'ordine, stati, ore (totali, attesa, effettive, risposta), permessi, elenco."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from metodo.core.permessi import F
from metodo.ticket import servizi
from metodo.ticket.models import Priorita, Stato, Ticket
from tests.test_report import analista, dati  # noqa: F401

pytestmark = pytest.mark.django_db


def _utente(nome, *codici):
    u = User.objects.create_user(nome, first_name=nome.capitalize())
    u.user_permissions.add(*Permission.objects.filter(codename__in=codici))
    return User.objects.get(pk=u.pk)


@pytest.fixture
def addetto():
    return _utente("mario", F.TICKET_VEDERE, F.TICKET_APRIRE, F.ORDINI_VEDERE)


@pytest.fixture
def capo():
    return _utente("lucia", F.TICKET_VEDERE, F.TICKET_GESTIRE, F.ORDINI_VEDERE)


def _sposta(t, **ore):
    """Porta indietro nel tempo le date del ticket (come se fosse aperto da tanto)."""
    delta = dt.timedelta(**ore)
    for campo in ("aperto_il", "preso_in_carico_il", "in_attesa_dal", "chiuso_il"):
        if getattr(t, campo):
            setattr(t, campo, getattr(t, campo) - delta)
    t.save()


def test_ore_con_attesa_e_chiusura(addetto, dati):
    riga = dati["aperto"].righe.first()
    t = servizi.apri(addetto, titolo="Viti mancanti", categoria="MAT", riga=riga)
    assert t.ordine == dati["aperto"] and t.stato == Stato.APERTO
    adesso = timezone.now()
    Ticket.objects.filter(pk=t.pk).update(aperto_il=adesso - dt.timedelta(hours=10))
    t.refresh_from_db()
    servizi.cambia_stato(t, addetto, Stato.LAVORAZIONE)
    t.refresh_from_db()
    assert t.assegnato_a == addetto and t.preso_in_carico_il
    servizi.cambia_stato(t, addetto, Stato.ATTESA, "fornitore")
    t.refresh_from_db()
    Ticket.objects.filter(pk=t.pk).update(in_attesa_dal=t.in_attesa_dal - dt.timedelta(hours=4))
    t.refresh_from_db()
    with pytest.raises(ValidationError):
        servizi.cambia_stato(t, addetto, Stato.CHIUSO)  # senza soluzione
    servizi.cambia_stato(t, addetto, Stato.CHIUSO, "Arrivate")
    t.refresh_from_db()
    o = servizi.ore(t)
    assert (o.totali, o.attesa, o.effettive) == (Decimal("10.0"), Decimal("4.0"), Decimal("6.0"))
    assert o.risposta == Decimal("10.0") and not o.in_corso and not o.in_ritardo
    # Le ore restano ferme dopo la chiusura.
    assert servizi.ore(t, timezone.now() + dt.timedelta(days=5)).totali == Decimal("10.0")
    assert [e.tipo for e in t.eventi.all()] == ["APE", "ASS", "STA", "STA", "STA"]


def test_ritardo_sulla_soglia_della_priorita(addetto):
    t = servizi.apri(addetto, titolo="Pressa ferma", priorita=Priorita.URGENTE)
    Ticket.objects.filter(pk=t.pk).update(aperto_il=timezone.now() - dt.timedelta(hours=5))
    t.refresh_from_db()
    o = servizi.ore(t)
    assert o.soglia == 4 and o.in_ritardo and o.in_corso


def test_permessi(addetto, capo):
    altro = _utente("gino", F.TICKET_VEDERE, F.TICKET_APRIRE)
    solo_vede = _utente("vede", F.TICKET_VEDERE)
    t = servizi.apri(addetto, titolo="Disegno da chiarire")
    with pytest.raises(PermissionDenied):
        servizi.apri(solo_vede, titolo="x")
    with pytest.raises(PermissionDenied):
        servizi.cambia_stato(t, altro, Stato.ATTESA)  # né aperto né assegnato a lui
    with pytest.raises(PermissionDenied):
        servizi.assegna(t, addetto, altro)  # assegnare è da capi
    servizi.assegna(t, capo, altro)
    t.refresh_from_db()
    servizi.cambia_stato(t, altro, Stato.CHIUSO, "Fatto")  # ora è suo
    t.refresh_from_db()
    with pytest.raises(PermissionDenied):
        servizi.cambia_stato(t, altro, Stato.APERTO)  # riapre chi l'ha aperto o un capo
    servizi.cambia_stato(t, addetto, Stato.APERTO, "Non va")
    t.refresh_from_db()
    assert t.stato == Stato.APERTO and t.chiuso_il is None and t.soluzione == ""


def test_pagine_e_apertura_dall_ordine(client, addetto, dati):
    ordine = dati["aperto"]
    client.force_login(addetto)
    pagina = client.get(ordine.get_absolute_url()).content.decode()
    assert f"/ticket/nuovo/?ordine={ordine.pk}" in pagina and f"/ticket/ordine/{ordine.pk}/" in pagina
    r = client.get(f"/ticket/nuovo/?ordine={ordine.pk}")
    assert r.status_code == 200 and ordine.codice_breve in r.content.decode()
    riga = ordine.righe.first()
    r = client.post(
        "/ticket/nuovo/",
        {"titolo": "Graffi", "categoria": "QUA", "priorita": "3", "ordine": ordine.pk, "riga": riga.pk},
    )
    t = Ticket.objects.get()
    assert r.status_code == 302 and r["Location"] == t.get_absolute_url() and t.riga == riga
    pannello = client.get(f"/ticket/ordine/{ordine.pk}/").content.decode()
    assert "Graffi" in pannello
    dettaglio = client.get(t.get_absolute_url()).content.decode()
    assert "Prendi in carico" in dettaglio and "Chiudi il ticket" in dettaglio
    client.post(f"/ticket/{t.pk}/azione/", {"azione": "commenta", "testo": "Visti 3 pezzi"})
    client.post(f"/ticket/{t.pk}/azione/", {"azione": "stato", "stato": "CHI", "nota": "Rilavorati"})
    t.refresh_from_db()
    assert t.is_chiuso and t.eventi.filter(testo="Visti 3 pezzi").exists()
    elenco = client.get("/ticket/").content.decode()
    assert "Graffi" not in elenco  # di base solo i non chiusi
    assert "Graffi" in client.get("/ticket/", {"stato": ""}).content.decode()
    assert client.get("/ticket/", {"stato": "", "formato": "xlsx"}).status_code == 200


def test_menu_e_permessi_ereditati(addetto):
    from metodo.core.menu import menu_utente

    menu = menu_utente(addetto, "/ticket/", None)
    assert menu["attiva"]["etichetta"] == "Ticket" and menu["attiva"]["sezione"] == "Produzione"
    from metodo.core.permessi import EREDITATI, codici_profilo

    assert EREDITATI[F.TICKET_VEDERE] == F.ORDINI_VEDERE
    assert {F.TICKET_VEDERE, F.TICKET_APRIRE} <= codici_profilo("Operatore")
    assert F.TICKET_GESTIRE in codici_profilo("Caporeparto")


def test_al_migrate_i_ticket_arrivano_a_chi_vede_gli_ordini():
    from django.contrib.auth.models import Group

    from metodo.core.permessi import GRUPPO_CLIENTE, sincronizza_permessi

    Permission.objects.filter(codename__startswith="ticket_").delete()  # come prima dell'aggiornamento
    vede = _utente("anna", F.ORDINI_VEDERE)
    capo = _utente("bruno", F.ORDINI_VEDERE, F.ORDINI_DIVIDI)
    cliente = _utente("cliente", F.ORDINI_VEDERE)
    cliente.groups.add(Group.objects.get_or_create(name=GRUPPO_CLIENTE)[0])
    sincronizza_permessi()

    def codici(u):
        return set(u.user_permissions.filter(codename__startswith="ticket_").values_list("codename", flat=True))

    assert codici(vede) == {F.TICKET_VEDERE, F.TICKET_APRIRE}
    assert codici(capo) == {F.TICKET_VEDERE, F.TICKET_APRIRE, F.TICKET_GESTIRE}
    assert codici(cliente) == set()
