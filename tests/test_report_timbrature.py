# ruff: noqa: F811  (fixture importate)
"""I report prendono le ore lavorate dalle timbrature (stesso calcolo della sezione Timbrature)."""

import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone

from metodo.core.models import Reparto
from metodo.pianificazione.calendario import lunedi
from metodo.report import servizi
from metodo.timbrature.models import Lavoratore, Timbratura

from .test_report import analista, dati  # noqa: F401  (fixture: reparto Pulsanti, righe consegnate oggi)

OGGI = timezone.localdate()
LUN = lunedi(OGGI)


def _alle(giorno, ora):
    return timezone.make_aware(dt.datetime.combine(giorno, dt.time(ora)))


@pytest.fixture
def timbrature(dati):
    pls = dati["rep"]
    ett = Reparto.objects.create(codice="ETT", nome="Elettronica")
    for matricola in ("A1", "A2"):
        lav = Lavoratore.objects.create(nome="N", cognome=matricola, matricola=matricola)
        for tipo, ora, rep in (
            ("ENT", 8, None),
            ("IRP", 8, pls),
            ("IME", 12, None),
            ("FME", 13, None),
            ("IRP", 13, ett),
            ("USC", 17, None),
        ):
            Timbratura.objects.create(lavoratore=lav, tipo=tipo, momento=_alle(LUN, ora), reparto=rep)
    return pls, ett


@pytest.mark.django_db
def test_ore_lavorate_dalle_timbrature_e_correzione(timbrature, analista):
    ore = servizi.ore_timbrate([LUN])[LUN]
    # 2 lavoratori × (4 h pulsanti + 4 h elettronica), mensa esclusa
    assert ore["totale"] == Decimal("16.00")
    assert ore["per_reparto"] == {"Pulsanti": Decimal("8.00"), "Elettronica": Decimal("8.00")}
    (s,) = servizi.produttivita([LUN])
    assert (s.ore_timbrate, s.ore_lavorate, s.fonte_ore) == (Decimal("16.00"), Decimal("16.00"), "dalle timbrature")
    assert s.efficienza is not None and s.pezzi_ora == (s.pezzi_totali / 16).quantize(Decimal("0.01"))
    assert s.resa_oraria == (s.valore / 16).quantize(Decimal("0.01"))  # €/ora reali
    servizi.salva_ore_lavorate(LUN, "20", analista)
    (s,) = servizi.produttivita([LUN])
    assert (s.ore_lavorate, s.fonte_ore) == (Decimal("20"), "corrette a mano")
    servizi.salva_ore_lavorate(LUN, "", analista)  # vuota: tornano le timbrature
    assert servizi.produttivita([LUN])[0].ore_lavorate == Decimal("16.00")


@pytest.mark.django_db
def test_pagina_produttivita_con_resa_oraria(client, timbrature, analista):
    client.force_login(analista)
    pagina = client.get(f"/report/produttivita/?fino_a={LUN}&settimane=1").content.decode()
    assert "Resa €/ora" in pagina
    xlsx = client.get(f"/report/produttivita/?fino_a={LUN}&settimane=1&formato=xlsx")
    assert xlsx.status_code == 200


@pytest.mark.django_db
def test_timbrate_negli_altri_report(timbrature, client, analista):
    righe, _ = servizi.materiale_per_reparto([LUN])
    per_nome = {r["reparto"]: r for r in righe}
    assert per_nome["Elettronica"]["tot_timbrate"] == Decimal("8.00")  # reparto senza materiale: c'è lo stesso
    assert per_nome["Pulsanti"]["celle"][0]["timbrate"] == Decimal("8.00")
    dati_settimane = servizi.evaso_consegnato_per_settimana([LUN])
    assert dati_settimane["totale_timbrate"] == Decimal("16.00")
    settimana = next(s for s in servizi.dashboard(OGGI)["settimane"] if s["lunedi"] == LUN)
    assert settimana["ore"] == Decimal("16.00") and settimana["efficienza"] is not None
    client.force_login(analista)
    for url in ("/report/", "/report/produttivita/", "/report/materiale/", "/report/settimane/"):
        assert client.get(url).status_code == 200, url
    assert "dalle timbrature" in client.get("/report/produttivita/").content.decode()
    assert client.get("/report/settimane/?formato=xlsx").status_code == 200
