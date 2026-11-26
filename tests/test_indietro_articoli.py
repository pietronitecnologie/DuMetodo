# ruff: noqa: F811
"""Pulsante «indietro» comune (pagina precedente) e Ricerca articoli."""

from __future__ import annotations

import pytest

from metodo.core.cronologia import aggiorna
from tests.test_report import analista, dati  # noqa: F401

pytestmark = pytest.mark.django_db


def test_pila_della_cronologia():
    s = aggiorna([], "/ordini/?anno=2026", "Ordini")
    s = aggiorna(s, "/ordini/?anno=2025")  # stessa pagina, altri filtri: sostituisce
    assert s == [{"url": "/ordini/?anno=2025", "titolo": "Ordini"}]
    s = aggiorna(s, "/ordini/5/", "Ordine 5")
    s = aggiorna(s, "/ordini/6/", "Ordine 6")
    assert [p["url"] for p in s] == ["/ordini/?anno=2025", "/ordini/5/", "/ordini/6/"]
    s = aggiorna(s, "/ordini/5/")  # da un link: come il browser, si aggiunge
    assert len(s) == 4
    s = aggiorna(s, "/ordini/?anno=2025", ritorno=True)  # con «indietro» o dopo un salvataggio: si accorcia
    assert [p["url"] for p in s] == ["/ordini/?anno=2025"]


def test_indietro_torna_alla_pagina_precedente(client, analista, dati):
    ordine = dati["aperto"]
    client.force_login(analista)
    client.get("/ordini/?anno=2026&solo_aperti=on")
    pagina = client.get(ordine.get_absolute_url()).content.decode()
    assert '?a=%2Fordini%2F%3Fanno%3D2026%26solo_aperti%3Don"' in pagina and "← Ordini</a>" in pagina
    client.get("/pianificazione/agenda/?q=2")
    pagina = client.get(ordine.get_absolute_url()).content.decode()
    assert '?a=%2Fpianificazione%2Fagenda%2F%3Fq%3D2"' in pagina
    # Dopo un ricaricamento (o un'azione che torna sulla pagina) resta la stessa pagina di provenienza.
    pagina = client.get(ordine.get_absolute_url()).content.decode()
    assert '?a=%2Fpianificazione%2Fagenda%2F%3Fq%3D2"' in pagina
    # Da una pagina qualsiasi, non solo dalle liste ordini.
    client.get("/report%2F")
    pagina = client.get(ordine.get_absolute_url()).content.decode()
    assert '?a=%2Freport%2F"' in pagina


def test_pulsante_indietro_e_dopo_salvataggio(client, analista, dati):
    from metodo.core.cronologia import CHIAVE

    ordine = dati["aperto"]
    client.force_login(analista)
    client.get("/ordini/?anno=2026")
    client.get(ordine.get_absolute_url())
    client.get("/report%2F")
    r = client.get("/indietro/", {"a": ordine.get_absolute_url()})
    assert r.status_code == 302 and r["Location"] == ordine.get_absolute_url()
    pagina = client.get(ordine.get_absolute_url()).content.decode()
    assert "?a=%2Fordini%2F%3Fanno%3D2026" in pagina  # il report è uscito dalla pila
    assert [p["url"] for p in client.session[CHIAVE]] == ["/ordini/?anno=2026", ordine.get_absolute_url()]
    assert client.get("/indietro/", {"a": "//altro.sito/"})["Location"] == "/"


def test_filtri_htmx_aggiornano_la_pagina_di_provenienza(client, analista, dati):
    client.force_login(analista)
    client.get("/ordini/")
    client.get("/ordini/?anno=2026", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="tabella-ordini")
    pagina = client.get(dati["aperto"].get_absolute_url()).content.decode()
    assert '?a=%2Fordini%2F%3Fanno%3D2026"' in pagina


def test_ricerca_articoli(client, analista, dati):
    client.force_login(analista)
    pagina = client.get("/ordini/articoli/").content.decode()
    assert "Imposta almeno un filtro" in pagina
    riga = dati["aperto"].righe.first()
    pagina = client.get("/ordini/articoli/", {"articolo": riga.articolo}).content.decode()
    assert riga.articolo in pagina and dati["aperto"].codice_breve in pagina
    xlsx = client.get("/ordini/articoli/", {"articolo": riga.articolo, "formato": "xlsx"})
    assert xlsx["Content-Type"].startswith("application/vnd.openxmlformats")


def test_marcatura_riga_lista_ordini(client, analista, dati):
    ordine = dati["aperto"]
    client.force_login(analista)
    url = f"/ordini/{ordine.pk}/marca/"
    assert client.get(url).status_code == 405
    assert client.post(url).json() == {"marcato": ordine.pk}
    pagina = client.get("/ordini/", {"anno": 2026}).content.decode()
    assert 'class="dm-marcata"' in pagina and pagina.count("dm-marcata") == 1
    assert client.post(url).json() == {"marcato": None}  # secondo clic: tolta
    assert "dm-marcata" not in client.get("/ordini/", {"anno": 2026}).content.decode()
    client.post(url)
    client.logout()  # uscendo si perde
    client.force_login(analista)
    assert "dm-marcata" not in client.get("/ordini/", {"anno": 2026}).content.decode()
