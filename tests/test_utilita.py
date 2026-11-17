import datetime as dt

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from dumetodo.comunicazioni.servizi import aggiungi_nota
from dumetodo.consegne import servizi as consegne
from dumetodo.core.permessi import F, applica_profilo, permessi_di
from dumetodo.ordini.models import Ordine, RigaOrdine, StatoOrdine, StoricoScadenza
from dumetodo.ordini.servizi import OperazioneNonConsentita
from dumetodo.utilita import servizi
from dumetodo.utilita.models import Annuncio


@pytest.fixture
def admin_u(db):
    u = User.objects.create_user("capo")
    applica_profilo(u, "Amministratore")
    return User.objects.get(pk=u.pk)


@pytest.fixture
def consegnato(ordine, operatore):
    from dumetodo.ordini import servizi as ordini

    ordini.cambia_stato(ordine.pk, StatoOrdine.IN_SPEDIZIONE, operatore)
    c = consegne.consegna(operatore, ordini_ids=[ordine.pk])
    consegne.registra_ddt("42", dt.date(2026, 5, 4), list(c.righe.values_list("pk", flat=True)), operatore)
    ordine.refresh_from_db()
    return ordine


def test_pulizia_html_annunci():
    sporco = (
        '<p onclick="x()">Ciao <b>tutti</b><script>alert(1)</script><a href="javascript:alert(1)">x</a>'
        '<a href="https://ok.it">ok</a><img src=x onerror=alert(1)></p>'
    )
    pulito = servizi.pulisci_html(sporco)
    assert "script" not in pulito and "onclick" not in pulito and "javascript" not in pulito and "<img" not in pulito
    assert "<b>tutti</b>" in pulito and 'href="https://ok.it"' in pulito


def test_annunci_scadenza(admin_u):
    a = servizi.salva_annuncio(admin_u, None, data=dt.date.today(), testo="<b>Ciao</b>", attivo=True, permanente=False)
    assert list(Annuncio.objects.visibili()) == [a]
    domani = timezone.now() + dt.timedelta(days=1, minutes=1)
    assert not Annuncio.objects.visibili(domani).exists()  # scade da solo, niente job
    a.permanente = True
    a.save()
    assert Annuncio.objects.visibili(domani).exists()
    servizi.attiva_disattiva(admin_u, a.pk)
    assert not Annuncio.objects.visibili().exists()
    with pytest.raises(OperazioneNonConsentita):
        servizi.salva_annuncio(
            admin_u, None, data=dt.date.today(), testo="<script>x</script>", attivo=True, permanente=False
        )


def test_annunci_visibili_solo_al_fornitore(client, admin_u, cliente, operatore):
    servizi.salva_annuncio(admin_u, None, data=dt.date.today(), testo="Riunione alle 14", attivo=True, permanente=True)
    client.force_login(cliente)
    assert client.get("/utilita/annunci/attivi/").content == b""
    client.force_login(operatore)
    assert b"Riunione alle 14" in client.get("/utilita/annunci/attivi/").content


def test_ristampa_etichette(client, consegnato, admin_u):
    assert list(servizi.ordini_per_ddt("42", 2026)) == [consegnato]
    assert servizi.anni_del_ddt("42") == [2026]
    client.force_login(admin_u)
    r = client.get("/utilita/ristampa-etichette/?numero=42&anno=2025")
    assert b"2026" in r.content  # suggerisce l'anno giusto
    r = client.post("/utilita/ristampa-etichette/", {"numero": "42", "anno": "2026", "ordine": [consegnato.pk]})
    assert r["Content-Disposition"] == 'attachment; filename="Etichette_DDT_42_2026.xlsx"'


def test_scorporazione_di_massa(consegnato, admin_u, operatore):
    righe = list(servizi.righe_consegnate("ART", "", dt.date.today(), dt.date.today()))
    assert len(righe) == 2
    ids = [r.pk for r in righe]
    assert servizi.scorpora_massa(admin_u, ids, ids[:1]) == 1
    assert RigaOrdine.objects.get(pk=ids[0]).scorporata
    with pytest.raises(OperazioneNonConsentita):
        servizi.scorpora_massa(admin_u, ids[:1], ids)  # id non visualizzati
    with pytest.raises(OperazioneNonConsentita):
        servizi.scorpora_massa(operatore, ids, [])  # permesso mancante
    altro = RigaOrdine.objects.create(ordine=consegnato, n_riga="9", articolo="X", qta=1)  # non consegnata
    assert servizi.scorpora_massa(admin_u, [altro.pk], [altro.pk]) == 0


def test_cambia_ordine_mantiene_i_collegamenti(consegnato, admin_u, operatore):
    aggiungi_nota(consegnato, operatore, "nota")
    Ordine.objects.create(serie="00040", numero="0009999", appendice="", anno=2026)
    with pytest.raises(OperazioneNonConsentita, match="Esiste già"):
        servizi.cambia_ordine(
            admin_u, consegnato.pk, numero="0009999", serie="00040", appendice="0", data_scadenza=None, motivo=""
        )
    o = servizi.cambia_ordine(
        admin_u,
        consegnato.pk,
        numero="0004300",
        serie="00041",
        appendice="t1",
        data_scadenza=dt.date(2026, 6, 1),
        motivo="",
    )
    assert o.codice == "00041.0004300.T1-2026"
    assert o.righe.count() == 2 and o.note.count() == 1 and consegne.ordini_del_ddt("42", 2026).get() == o
    assert StoricoScadenza.objects.filter(
        ordine=o, nuova=dt.date(2026, 6, 1), motivo__contains="00040.0004300"
    ).exists()
    # le righe già in bolla mantengono la loro scadenza
    assert not o.righe.filter(data_scadenza=dt.date(2026, 6, 1)).exists()


def test_permessi_utilita(client, consegnato, operatore):
    u = User.objects.create_user("solo_annunci")
    u.user_permissions.set(permessi_di([F.UTILITA_ANNUNCI]))
    client.force_login(u)
    assert client.get("/utilita/").status_code == 200
    assert client.get("/utilita/annunci/").status_code == 200
    assert client.get("/utilita/scorporazione/").status_code == 403
    assert client.get(f"/utilita/cambia-ordine/{consegnato.pk}/").status_code == 403
    r = client.post("/utilita/annunci/nuovo/", {"data": "2026-05-04", "testo": "<p>Ok</p>", "attivo": "on"})
    assert r.status_code == 302 and Annuncio.objects.get().testo == "<p>Ok</p>"
    assert client.get("/utilita/annunci/1/attiva/").status_code == 405


def test_pagine_utilita(client, consegnato, admin_u):
    client.force_login(admin_u)
    for url in (
        "/utilita/",
        "/utilita/annunci/nuovo/",
        "/utilita/scorporazione/?articolo=ART",
        "/utilita/cambia-ordine/?q=4300",
        f"/utilita/cambia-ordine/{consegnato.pk}/",
    ):
        assert client.get(url).status_code == 200, url
