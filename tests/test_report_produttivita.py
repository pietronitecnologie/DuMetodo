"""Report settimanali per il controllo della produttività: efficienza per reparto, puntualità, ore per lavoratore."""

import datetime as dt
import io
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from openpyxl import load_workbook

from dumetodo.core.permessi import applica_profilo
from dumetodo.ordini.models import RigaOrdine
from dumetodo.report import servizi
from dumetodo.timbrature.models import Lavoratore, Timbratura

from .test_report import LUN, analista, dati  # noqa: F401  (fixture)

PREC = LUN - dt.timedelta(days=7)  # settimana scorsa: tutto è già passato, qualunque giorno sia oggi
MARTEDI = PREC + dt.timedelta(days=1)


def _ora(giorno: dt.date, h: int) -> dt.datetime:
    return timezone.make_aware(dt.datetime.combine(giorno, dt.time(h)))


@pytest.fixture
def produzione(dati):  # noqa: F811
    """Consegna di martedì scorso (scadenza lunedì: un giorno di ritardo) e un turno di 5 ore, 4 in Pulsanti."""
    RigaOrdine.objects.filter(ordine=dati["consegnato"]).update(data_consegna=MARTEDI, data_scadenza=PREC)
    RigaOrdine.objects.filter(ordine=dati["aperto"]).update(data_scadenza=PREC)  # scadute, ancora da consegnare
    mario = Lavoratore.objects.create(nome="Mario", cognome="Rossi", matricola="M1", costo_orario=Decimal("20"))
    Lavoratore.objects.create(nome="Ex", cognome="Dipendente", matricola="M2", attivo=False)
    rep = dati["rep"]
    for h, tipo, r in ((8, "ENT", None), (8, "IRP", rep), (12, "FRP", rep), (13, "USC", None)):
        Timbratura.objects.create(lavoratore=mario, tipo=tipo, momento=_ora(MARTEDI, h), reparto=r)
    return {**dati, "mario": mario}


def test_efficienza_per_reparto(produzione):
    d = servizi.efficienza_reparti(servizi.settimane_indietro(2), con_costi=True)
    assert d["reparti"] == ["Pulsanti"] and d["resa_configurata"] and d["con_timbrature"]
    corrente, scorsa = d["righe"]  # dalla più recente
    assert corrente["lunedi"] == LUN and corrente["timbrate"] is None
    [c] = scorsa["celle"]
    # prodotte: (10×2,5 + 5×4) / resa 10 = 4,5 h; timbrate in reparto 4 h -> 112%
    assert (c["timbrate"], c["prodotte"], c["efficienza"], c["classe"]) == (
        Decimal("4.00"),
        Decimal("4.50"),
        Decimal("112"),
        "ok",
    )
    assert (scorsa["timbrate"], scorsa["non_assegnate"], scorsa["efficienza"], scorsa["classe"]) == (
        Decimal("5.00"),
        Decimal("1.00"),
        Decimal("90"),
        "medio",
    )
    assert scorsa["costo"] == Decimal("100.00") and scorsa["costo_ora_prodotta"] == Decimal("22.22")
    assert "costo" not in servizi.efficienza_reparti(servizi.settimane_indietro(2))["righe"][1]


def test_puntualita_e_arretrato(produzione):
    corrente, scorsa = servizi.puntualita_arretrato(servizi.settimane_indietro(2))
    assert (scorsa["consegnate"], scorsa["puntuali"], scorsa["in_ritardo"]) == (2, 0, 2)
    assert scorsa["puntualita"] == 0 and scorsa["classe"] == "basso" and scorsa["ritardo_medio"] == Decimal("1.0")
    # le righe aperte sono state create oggi: alla fine della settimana scorsa non esistevano ancora
    assert scorsa["arretrato_righe"] == 0
    # oggi sono scadute e non consegnate: 3×1,5 + 1×2 = 6,5 € -> 0,65 h
    assert corrente["arretrato_righe"] == 2 and corrente["arretrato_ore"] == Decimal("0.65")
    assert corrente["consegnate"] == 0 and corrente["puntualita"] is None


def test_ore_per_lavoratore(produzione):
    d = servizi.ore_per_lavoratore(servizi.settimane_indietro(2))
    assert d["settimane"] == [LUN, PREC]
    [r] = d["righe"]  # il lavoratore non attivo e senza ore non compare
    assert r["lavoratore"] == produzione["mario"]
    assert r["celle"][0]["ore"] is None and r["celle"][1]["ore"] == Decimal("5.00")
    assert r["celle"][1]["in_reparto"] == Decimal("80") and r["celle"][1]["classe"] == "medio"
    assert r["totale"] == Decimal("5.00") and r["media"] == Decimal("2.5") and r["anomalie"] == 0


def test_viste_excel_e_permessi(client, produzione, analista, operatore):  # noqa: F811
    client.force_login(analista)
    for url, testo in (
        ("/report/efficienza-reparti/?settimane=2", "Efficienza per reparto"),
        ("/report/puntualita/?settimane=2", "Puntualità e arretrato"),
        ("/report/ore-lavoratori/?settimane=2", "Rossi Mario"),
    ):
        r = client.get(url)
        assert r.status_code == 200 and testo in r.content.decode(), url
        x = client.get(url + "&formato=xlsx")
        assert x.status_code == 200 and x["Content-Type"].startswith("application/vnd.openxmlformats")
        assert load_workbook(io.BytesIO(x.content)).active.max_row >= 2
    assert "costo €" in client.get("/report/efficienza-reparti/").content.decode()  # Amministratore: costi visibili

    capo = User.objects.create_user("capo-rep")
    applica_profilo(capo, "Caporeparto")
    client.force_login(User.objects.get(pk=capo.pk))
    pagina = client.get("/report/efficienza-reparti/")
    if pagina.status_code == 200:
        assert "costo €" not in pagina.content.decode()  # senza il permesso dei costi
    client.force_login(operatore)
    for url in ("/report/efficienza-reparti/", "/report/puntualita/", "/report/ore-lavoratori/"):
        assert client.get(url).status_code == 403, url
