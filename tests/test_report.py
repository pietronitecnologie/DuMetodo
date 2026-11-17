import io
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from dumetodo.anagrafica.models import Articolo
from dumetodo.configurazione import servizi as config
from dumetodo.consegne import servizi as consegne
from dumetodo.core.models import Reparto
from dumetodo.core.permessi import F, applica_profilo
from dumetodo.ordini.models import Ordine, RigaOrdine, StatoOrdine
from dumetodo.ordini.servizi import OperazioneNonConsentita
from dumetodo.pianificazione.calendario import lunedi
from dumetodo.report import servizi
from dumetodo.report.models import OreLavorate

OGGI = timezone.localdate()
LUN = lunedi(OGGI)


@pytest.fixture
def analista(db):
    u = User.objects.create_user("analista")
    applica_profilo(u, "Amministratore")
    return User.objects.get(pk=u.pk)


@pytest.fixture
def dati(db, operatore):
    config.scrivi("produzione.prezzo_resa_oraria", "10")
    config.invalida_cache()
    rep = Reparto.objects.create(codice="PLS", nome="Pulsanti")
    Articolo.objects.create(codice="P1", reparto=rep, prezzo_riferimento=Decimal("2.0000"))
    Articolo.objects.create(codice="P2", reparto=rep)
    o = Ordine.objects.create(
        serie="00040", numero="1", appendice="T1", anno=2026, stato=StatoOrdine.IN_SPEDIZIONE, data_scadenza=OGGI
    )
    RigaOrdine.objects.create(
        ordine=o, n_riga="1", articolo="P1", qta=10, prezzo_unitario=Decimal("2.5"), data_scadenza=OGGI
    )
    RigaOrdine.objects.create(
        ordine=o, n_riga="2", articolo="P2", qta=5, prezzo_unitario=Decimal("4"), data_scadenza=OGGI
    )
    consegna = consegne.consegna(operatore, ordini_ids=[o.pk])
    consegne.registra_ddt("9", OGGI, list(consegna.righe.values_list("pk", flat=True)), operatore)
    aperto = Ordine.objects.create(serie="00040", numero="2", appendice="", anno=2026, data_scadenza=OGGI)
    RigaOrdine.objects.create(
        ordine=aperto, n_riga="1", articolo="P1", qta=3, prezzo_unitario=Decimal("1.5"), data_scadenza=OGGI
    )
    RigaOrdine.objects.create(
        ordine=aperto, n_riga="2", articolo="P1", qta=1, prezzo_unitario=Decimal("2"), data_scadenza=OGGI
    )
    return {"rep": rep, "consegnato": o, "aperto": aperto}


def test_categoria():
    assert [servizi.categoria(a) for a in ("T1", "R2", "CL", "", "X")] == [
        "Pulsanti (T)",
        "Elettronica (R)",
        "Conto lavoro",
        "Ordini Cliente",
        "Altro",
    ]


def test_dashboard(dati):
    d = servizi.dashboard()
    assert d["in_ritardo"] == 0 and d["senza_ddt"] == 0
    assert d["settimane"][-1]["pezzi"] == 15 and d["settimane"][-1]["percentuale"] == 100


def test_produttivita_e_ore_lavorate(dati, analista, operatore):
    servizi.salva_ore_lavorate(OGGI, "7,5", analista)  # si salva sul lunedì
    with pytest.raises(OperazioneNonConsentita):
        servizi.salva_ore_lavorate(OGGI, "1", operatore)
    with pytest.raises(OperazioneNonConsentita):
        servizi.salva_ore_lavorate(OGGI, "-1", analista)
    [s] = servizi.produttivita([LUN])
    assert s.pezzi["Pulsanti (T)"] == 15 and s.valore == Decimal("45") and s.ore_teoriche == Decimal("4.50")
    assert s.ore_lavorate == Decimal("7.5") and s.pezzi_ora == Decimal("2.00") and s.efficienza == Decimal("60.0")
    assert OreLavorate.objects.get().settimana == LUN
    servizi.salva_ore_lavorate(OGGI, "", analista)
    assert not OreLavorate.objects.exists()
    giorni = servizi.produttivita_giornaliera(LUN)
    assert sum(g["totale"] for g in giorni) == 15


def test_materiale(dati):
    righe, _ = servizi.materiale_per_reparto([LUN])
    [pls] = righe
    assert pls["celle"][0]["scadenza"] == Decimal("5.15")  # (25 + 20 + 4,5 + 2) / 10
    assert pls["celle"][0]["consegnato"] == Decimal("4.50")


def test_controllo_prezzi(dati):
    bolle = list(servizi.controllo_prezzi(servizi.FiltriPrezzi(dal=OGGI, al=OGGI)))
    assert [(r.articolo, r.differenza, r.ddt_numero) for r in bolle] == [("P1", Decimal("0.5"), "9")]
    tutte = servizi.controllo_prezzi(servizi.FiltriPrezzi(dal=OGGI, al=OGGI, solo_differenze=False))
    assert tutte.count() == 2
    senza = servizi.controllo_prezzi(servizi.FiltriPrezzi(dal=OGGI, al=OGGI, senza_riferimento=True))
    assert [r.articolo for r in senza] == ["P2"]
    lav = servizi.controllo_prezzi(servizi.FiltriPrezzi(origine="lavorazione"))
    assert [(r.ordine, r.differenza) for r in lav] == [(dati["aperto"], Decimal("-0.5"))]  # la riga a 2 € è giusta
    assert servizi.totale_differenze(lav) == Decimal("-1.5")
    assert servizi.controllo_prezzi(servizi.FiltriPrezzi(numero_ddt="999")).count() == 0


def test_viste_report(client, dati, analista, operatore):
    client.force_login(operatore)
    for url in ("/report/", "/report/produttivita/", "/report/materiale/", "/report/prezzi/"):
        assert client.get(url).status_code == 403, url
    client.force_login(analista)
    for url in (
        "/report/",
        "/report/produttivita/",
        f"/report/produttivita/?dettaglio={OGGI}",
        "/report/materiale/",
        "/report/prezzi/",
        "/report/prezzi/?origine=lavorazione",
    ):
        assert client.get(url).status_code == 200, url
    for url in (
        "/report/produttivita/?formato=xlsx",
        "/report/materiale/?formato=xlsx",
        "/report/prezzi/?formato=xlsx&origine=lavorazione",
    ):
        r = client.get(url)
        assert r["Content-Type"].startswith("application/vnd.openxml"), url
    from openpyxl import load_workbook

    ws = load_workbook(io.BytesIO(client.get("/report/prezzi/?formato=xlsx&origine=lavorazione").content)).active
    assert ws.max_row == 2 and ws["A2"].value == dati["aperto"].codice
    r = client.post("/report/produttivita/ore/", {f"ore_{LUN}": "12", "settimane": "4", "fino_a": "javascript:x"})
    assert r["Location"] == "/report/produttivita/?settimane=4"
    assert OreLavorate.objects.get().ore == 12


def test_permesso_singolo(client, dati):
    from dumetodo.core.permessi import permessi_di

    u = User.objects.create_user("solo_prezzi")
    u.user_permissions.set(permessi_di([F.REPORT_PREZZI]))
    client.force_login(u)
    assert client.get("/report/prezzi/").status_code == 200
    assert client.get("/report/").status_code == 403
