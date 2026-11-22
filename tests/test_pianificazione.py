import datetime as dt
import io
from decimal import Decimal

import pytest
from django.core import mail
from django.utils import timezone

from dumetodo.anagrafica.models import Articolo, Tabulato, UnitaMisura
from dumetodo.comunicazioni.servizi import aggiungi_nota
from dumetodo.configurazione import servizi as config
from dumetodo.core.models import Reparto
from dumetodo.ordini.models import Ordine, RigaOrdine, StatoOrdine
from dumetodo.ordini.servizi import OperazioneNonConsentita
from dumetodo.pianificazione import calendario, piano, servizi

# Mercoledì 15 aprile 2026 ore 10:00 -> lunedì 13/04
ADESSO = timezone.make_aware(dt.datetime(2026, 4, 15, 10, 0))
LUN = dt.date(2026, 4, 13)


def test_ore_rimanenti_settimana():
    f = calendario.ore_rimanenti_settimana
    assert f(dt.datetime(2026, 4, 13, 10, 30), 8, 17) == Decimal("42.50")  # 6,5 + 4×9
    assert f(dt.datetime(2026, 4, 15, 7, 0), 8, 17) == Decimal("27.00")
    assert f(dt.datetime(2026, 4, 17, 18, 0), 8, 17) == 0
    assert f(dt.datetime(2026, 4, 18, 10, 0), 8, 17) == 0


@pytest.fixture
def scenario(db):
    config.scrivi("produzione.prezzo_resa_oraria", "10")
    config.invalida_cache()
    pls = Reparto.objects.create(codice="PLS", nome="Pulsanti", soglia_ore_settimanali=Decimal(45))
    ele = Reparto.objects.create(codice="ELE", nome="Elettronica", soglia_ore_settimanali=Decimal(10))
    Articolo.objects.create(codice="P1", reparto=pls, tabulato=Tabulato.T2)
    Articolo.objects.create(codice="E1", reparto=ele)
    Articolo.objects.create(codice="CAVO", reparto=pls, um=UnitaMisura.METRI)

    def ordine(numero, stato=StatoOrdine.INSERITO, appendice="T1", scad=LUN):
        return Ordine.objects.create(
            serie="00040", numero=numero, appendice=appendice, anno=2026, stato=stato, data_scadenza=scad
        )

    def riga(o, n, art, qta, prezzo="10", scad=LUN, **kw):
        return RigaOrdine.objects.create(
            ordine=o,
            n_riga=n,
            articolo=art,
            qta=Decimal(qta),
            prezzo_unitario=Decimal(prezzo) if prezzo else None,
            data_scadenza=scad,
            **kw,
        )

    a = ordine("1")
    riga(a, "1", "P1", 9)  # 9 h settimana 1
    riga(a, "2", "P1", 30, scad=LUN + dt.timedelta(days=8))  # 30 h settimana 2
    riga(a, "3", "SCONOSCIUTO", 1)  # 1 h senza reparto
    riga(a, "4", "P1", 1, prezzo=None)  # senza prezzo
    riga(a, "5", "P1", 5, bolla_emessa=True)  # già in bolla: escluso
    b = ordine("2", stato=StatoOrdine.EVASIONE_PARZIALE, scad=LUN - dt.timedelta(days=10))
    riga(b, "1", "E1", 2, scad=LUN - dt.timedelta(days=10))  # 2 h arretrato elettronica
    riga(ordine("3", stato=StatoOrdine.IN_LAVORAZIONE), "1", "P1", 50)  # LAV: escluso
    riga(ordine("4", appendice=""), "1", "P1", 50)  # ordine del cliente senza appendice: escluso
    c = ordine("5", scad=LUN + dt.timedelta(days=1))
    riga(c, "1", "CAVO", "12.5", prezzo="0.8", scad=LUN + dt.timedelta(days=1))
    riga(c, "2", "P1", 4, scad=LUN + dt.timedelta(days=1))
    return {"pls": pls, "ele": ele, "a": a, "b": b, "c": c}


def test_piano_di_carico(scenario):
    p = piano.calcola(ADESSO)
    righe = {r.nome: r for r in p.righe}
    pls = righe["Pulsanti"]
    assert pls.settimane[0].ore == Decimal("14.00")  # 9 + cavo 1 + 4
    assert pls.settimane[1].ore == Decimal("30.00")
    assert p.ore_rimanenti == Decimal("25.00")  # mercoledì 10:00 -> 7 + 2×9
    assert pls.settimane[0].capacita == Decimal("25.00") and pls.settimane[1].capacita == 45
    assert pls.settimane[1].percentuale == Decimal("66.7") and pls.settimane[1].livello(80) == "ok"
    ele = righe["Elettronica"]
    assert ele.arretrato.ore == 2 and ele.arretrato.n_ordini == 1
    assert righe["Senza reparto"].settimane[0].ore == 1  # in Metodo spariva
    assert p.righe_senza_prezzo == 1
    assert pls.settimane[0].n_ordini == 2


def test_piano_senza_resa_oraria(db):
    assert piano.calcola(ADESSO).resa_mancante


def test_raggruppamento_report_non_conta_due_volte(scenario):
    for r in (scenario["pls"], scenario["ele"]):
        r.gruppo_report = "Area Quadristica"
        r.save()
    RigaOrdine.objects.create(
        ordine=scenario["a"], n_riga="9", articolo="E1", qta=1, prezzo_unitario=10, data_scadenza=LUN
    )
    p = piano.calcola(ADESSO)
    [gruppo] = [r for r in piano.raggruppa_per_report(p) if r.nome == "Area Quadristica"]
    assert gruppo.settimane[0].ore == Decimal("15.00")
    assert gruppo.settimane[0].n_ordini == 2  # ordine 1 e 5, una volta sola
    assert gruppo.settimane[1].capacita == 55


def test_commesse_bloccate_e_xlsx(scenario, operatore):
    from openpyxl import load_workbook

    aggiungi_nota(scenario["a"], operatore, "Manca il componente X")
    aggiungi_nota(scenario["b"], operatore, "tutto ok")
    bloccate = servizi.commesse_bloccate()
    assert [b["ordine"] for b in bloccate] == [scenario["a"]] and "Manca" in bloccate[0]["note"]
    _nome, contenuto, _ = servizi.genera_xlsx(ADESSO)
    wb = load_workbook(io.BytesIO(contenuto))
    assert wb.sheetnames == ["Pianificazione", "Commesse bloccate"]
    ws = wb["Pianificazione"]
    assert ws["A6"].value in {"Pulsanti", "Elettronica", "Senza reparto"} and ws.freeze_panes == "B6"
    assert wb["Commesse bloccate"]["A5"].value == scenario["a"].codice


def test_invio_report(scenario, operatore, amministratore, django_capture_on_commit_callbacks):
    with pytest.raises(OperazioneNonConsentita, match="destinatario"):
        servizi.invia_report(operatore)
    config.scrivi_molti(
        {
            "email.report_produzione_to": "capo@x.it",
            "email.report_produzione_cc": "capo@x.it, cc@x.it",
            "email.mittente": "dumetodo@x.it",
        }
    )
    config.invalida_cache()
    with pytest.raises(OperazioneNonConsentita, match="indirizzi diversi"):
        servizi.invia_report(operatore, destinatari="esterno@altro.it")
    with django_capture_on_commit_callbacks(execute=True):
        servizi.invia_report(operatore, oggetto="Report\r\nBcc: x@y.it", messaggio="<b>ciao</b>")
    m = mail.outbox[0]
    assert m.to == ["capo@x.it"] and m.cc == ["cc@x.it"]  # CC già in A: rimosso
    assert m.subject == "Report Bcc: x@y.it"  # niente header injection
    assert "&lt;b&gt;ciao&lt;/b&gt;" in m.alternatives[0][0]
    assert m.attachments[0][0].startswith("pianificazione_")
    with django_capture_on_commit_callbacks(execute=True):
        servizi.invia_report(amministratore, destinatari="altro@x.it")
    assert mail.outbox[1].to == ["altro@x.it"]


def test_invio_automatico(scenario):
    from dumetodo.pianificazione.tasks import invia_report_pianificazione

    assert "destinatari" in invia_report_pianificazione()  # nessun destinatario: nessun errore, solo log
    config.scrivi("pianificazione.report_automatico", False)
    config.invalida_cache()
    assert invia_report_pianificazione() == "disattivato"


def test_aggiorna_soglia(scenario, operatore, caporeparto):
    with pytest.raises(OperazioneNonConsentita):
        servizi.aggiorna_soglia(scenario["pls"].pk, "40", operatore)
    for sbagliato in ("-1", "nan", "inf", "abc"):
        with pytest.raises(OperazioneNonConsentita):
            servizi.aggiorna_soglia(scenario["pls"].pk, sbagliato, caporeparto)
    assert servizi.aggiorna_soglia(scenario["pls"].pk, "37,5", caporeparto).soglia_ore_settimanali == Decimal("37.5")
    assert servizi.aggiorna_soglia(scenario["pls"].pk, "", caporeparto).soglia_ore_settimanali is None


def test_agenda(scenario):
    Ordine.objects.filter(pk=scenario["b"].pk).update(appendice="R1")
    colonne = servizi.agenda(Ordine.objects.all(), oggi=ADESSO.date())
    assert [len(c.ordini) for c in colonne] == [1, 4, 0, 0]  # 'b' arretrata; questa settimana 1, 3 (LAV), 4, 5
    config.scrivi("ordini.soglia_pezzi_commesse_grandi", 45)
    config.invalida_cache()
    pulsanti = servizi.agenda_pulsanti(Ordine.objects.all(), oggi=ADESSO.date())
    assert len(pulsanti) == 3
    questa = pulsanti[1]
    assert {o.numero for o in questa.ordini} == {"1", "3", "5"}
    # pezzi: '1' = 46 (tutte le righe attive), '3' = 50, '5' = 16,5
    assert {o.numero for o in questa.grandi} == {"1", "3"} and {o.numero for o in questa.piccole} == {"5"}


def test_tabulati(scenario):
    voci, totale = servizi.tabulato_settimanale(scenario["pls"], LUN)
    per_art = {(v.articolo, v.ordine.numero if v.ordine else None): v for v in voci}
    assert per_art[("P1", None)].qta == 14  # 9 + 1 (senza prezzo) + 4, sommati tra ordini
    assert per_art[("CAVO", "5")].qta == Decimal("12.5")  # metri: per ordine
    assert per_art[("P1", None)].ore == Decimal("13.00") and totale == Decimal("14.00")
    config.scrivi("pianificazione.soglia_pezzi_scorte", 45)
    config.invalida_cache()
    voci, _ = servizi.tabulato_scorte(scenario["pls"], LUN)
    per_art = {v.articolo: v for v in voci}
    # ordini INS/LAV con appendice e al massimo 45 pezzi non in bolla: '1' (41) e '5' (16,5); '3' (50) escluso
    assert per_art["P1"].qta == 44 and per_art["CAVO"].qta == Decimal("12.5")


def test_viste(client, scenario, operatore, cliente):
    client.force_login(cliente)
    assert client.get("/pianificazione/agenda/").status_code == 200
    assert client.get("/pianificazione/").status_code == 403
    client.force_login(operatore)
    for url in (
        "/pianificazione/",
        "/pianificazione/agenda/pulsanti/",
        f"/pianificazione/tabulati/settimanale/?reparto={scenario['pls'].pk}",
        f"/pianificazione/tabulati/scorte/?reparto={scenario['pls'].pk}&formato=stampa",
    ):
        assert client.get(url).status_code == 200, url
    for url in (
        "/pianificazione/export/",
        f"/pianificazione/tabulati/settimanale/?reparto={scenario['pls'].pk}&formato=xlsx",
    ):
        assert client.get(url)["Content-Type"] == servizi.XLSX, url
    assert client.post("/pianificazione/soglie/", {f"soglia_{scenario['pls'].pk}": "10"}).status_code == 403
    assert client.get("/pianificazione/report/").status_code == 405
