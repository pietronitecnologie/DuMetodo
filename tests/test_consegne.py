import datetime as dt
import io
from decimal import Decimal

import pytest
from django.core import mail

from dumetodo.configurazione import servizi as config
from dumetodo.consegne import servizi
from dumetodo.consegne.models import DDT, Consegna, RigaConsegnata
from dumetodo.ordini import servizi as ordini
from dumetodo.ordini.models import Ordine, RigaOrdine, StatoOrdine
from dumetodo.ordini.servizi import OperazioneNonConsentita


@pytest.fixture
def in_spedizione(ordine, operatore):
    ordini.cambia_stato(ordine.pk, StatoOrdine.IN_SPEDIZIONE, operatore)
    ordine.refresh_from_db()
    return ordine


def _ordine(serie="00042", numero="0000077", appendice="", **righe):
    o = Ordine.objects.create(
        serie=serie, numero=numero, appendice=appendice, anno=2026, stato=StatoOrdine.IN_SPEDIZIONE
    )
    for n, (art, qta) in enumerate(righe.items(), start=1):
        RigaOrdine.objects.create(
            ordine=o, n_riga=f"{n:07}", articolo=art, qta=Decimal(qta), prezzo_unitario=Decimal("1.5")
        )
    return o


def test_consegna_completa_genera_bolla_readypro(in_spedizione, operatore):
    c = servizi.consegna(operatore, ordini_ids=[in_spedizione.pk])
    assert bytes(c.file_contenuto) == (b"Riferimento |ordine 00040.0004300\r\nART-A||10|PZ|2,5\r\nART-B||5|PZ|1\r\n")
    assert c.file_nome.startswith("consegna40-")
    in_spedizione.refresh_from_db()
    assert in_spedizione.stato == StatoOrdine.CONSEGNATO and in_spedizione.data_consegna == dt.date.today()
    assert all(r.bolla_emessa and r.data_consegna for r in in_spedizione.righe.all())
    assert RigaConsegnata.objects.filter(consegna=c).count() == 2


def test_evasione_parziale_consegna_solo_le_righe_evase(ordine, operatore):
    _, b = ordine.righe.order_by("n_riga")
    ordini.azione_riga(b.pk, "blocca", operatore)
    ordini.cambia_stato(ordine.pk, StatoOrdine.IN_SPEDIZIONE, operatore)  # diventa SPP: a in evasione
    c = servizi.consegna(operatore, ordini_ids=[ordine.pk])
    assert list(c.righe.values_list("riga__articolo", flat=True)) == ["ART-A"]
    ordine.refresh_from_db()
    assert ordine.stato == StatoOrdine.EVASIONE_PARZIALE  # resta la riga bloccata
    b.refresh_from_db()
    assert not b.bolla_emessa
    # Consegnato solo se completo: resta aperto, ma senza righe da consegnare esce dall'area Consegna
    assert not servizi.ordini_da_consegnare().filter(pk=ordine.pk).exists()
    ordini.azione_riga(b.pk, "evadi", operatore)  # più avanti si evade anche la riga che era bloccata
    assert servizi.ordini_da_consegnare().filter(pk=ordine.pk).exists()
    servizi.consegna(operatore, ordini_ids=[ordine.pk])
    ordine.refresh_from_db()
    assert ordine.stato == StatoOrdine.CONSEGNATO and not servizi.ordini_da_consegnare().exists()


def test_area_consegna_mostra_solo_ordini_con_righe_da_consegnare(client, ordine, operatore):
    a, b = ordine.righe.order_by("n_riga")
    for r in (a, b):
        ordini.azione_riga(r.pk, "blocca", operatore)
    ordini.cambia_stato(ordine.pk, StatoOrdine.IN_SPEDIZIONE, operatore)
    ordine.refresh_from_db()
    assert ordine.stato in (StatoOrdine.IN_SPEDIZIONE, StatoOrdine.EVASIONE_PARZIALE)
    assert not servizi.ordini_da_consegnare().exists()  # tutte bloccate: niente da consegnare
    client.force_login(operatore)
    assert ordine.codice_breve not in client.get("/consegne/").content.decode()


def test_doppio_clic_non_consegna_due_volte(in_spedizione, operatore):
    servizi.consegna(operatore, ordini_ids=[in_spedizione.pk])
    with pytest.raises(OperazioneNonConsentita, match="Nessuna riga"):
        servizi.consegna(operatore, ordini_ids=[in_spedizione.pk])
    assert Consegna.objects.count() == 1


def test_selezione_per_serie(in_spedizione, operatore):
    altro = _ordine(**{"X": 3})
    c = servizi.consegna(operatore, serie=["42"])
    assert c.selezione == "42" and c.righe.get().riga.ordine == altro
    in_spedizione.refresh_from_db()
    assert in_spedizione.stato == StatoOrdine.IN_SPEDIZIONE


def test_consegna_usa_il_tracciato_configurato(in_spedizione, operatore):
    config.scrivi(
        "consegna.tracciato_bolla",
        "codifica: utf-8\nfine_riga: LF\nnome_file: bolla\n"
        "[riga]\n{ordine.numero|senza_zeri};{riga.articolo};{riga.qta|compatto}",
    )
    config.invalida_cache()
    c = servizi.consegna(operatore, ordini_ids=[in_spedizione.pk])
    assert (c.file_nome, bytes(c.file_contenuto)) == ("bolla.txt", b"4300;ART-A;10\n4300;ART-B;5\n")


def test_carattere_non_codificabile_annulla_tutta_la_consegna(in_spedizione, operatore):
    RigaOrdine.objects.filter(ordine=in_spedizione).update(articolo="ART-€Ω")  # Ω non esiste in cp1252
    with pytest.raises(OperazioneNonConsentita, match="rappresentabile"):
        servizi.consegna(operatore, ordini_ids=[in_spedizione.pk])
    assert not Consegna.objects.exists()
    assert not RigaOrdine.objects.filter(bolla_emessa=True).exists()


def test_cliente_non_consegna(in_spedizione, cliente):
    with pytest.raises(OperazioneNonConsentita):
        servizi.consegna(cliente, ordini_ids=[in_spedizione.pk])


# ----------------------------------------------------------------------------------------- DDT
def test_registra_ddt_invia_file_tecnico(in_spedizione, operatore, configura_email, django_capture_on_commit_callbacks):
    config.scrivi("email.file_tecnico", "dmg@example.com")
    config.invalida_cache()
    RigaOrdine.objects.filter(ordine=in_spedizione, n_riga="0000001").update(qta=Decimal("100.05"))
    c = servizi.consegna(operatore, ordini_ids=[in_spedizione.pk])
    ids = list(c.righe.values_list("pk", flat=True))
    with django_capture_on_commit_callbacks(execute=True):
        ddt = servizi.registra_ddt("123", dt.date(2026, 4, 2), ids, operatore)
    assert ddt.file_nome == "DDT_3649_2026_123.txt"
    assert bytes(ddt.file_contenuto) == (
        b"T+R@3649@02/04/2026@123@ART-A@@100,05@2026.40.4300.1,00@1\r\n"  # Metodo scriveva 1005
        b"RIG@3649@02/04/2026@123@ART-B@@5@2026.40.4300.2,00@1\r\n"
    )
    m = mail.outbox[0]
    assert m.to == ["dmg@example.com"] and m.attachments[0][0] == "DDT_3649_2026_123.txt"
    assert not servizi.righe_senza_ddt().exists()
    assert list(servizi.ordini_del_ddt("123", 2026)) == [in_spedizione]


def test_ddt_numero_anno_unico_e_righe_non_riassegnabili(in_spedizione, operatore):
    c = servizi.consegna(operatore, ordini_ids=[in_spedizione.pk])
    a, b = c.righe.values_list("pk", flat=True)
    servizi.registra_ddt("10", dt.date(2026, 1, 5), [a], operatore)
    with pytest.raises(OperazioneNonConsentita, match="già registrato"):
        servizi.registra_ddt("10", dt.date(2026, 6, 1), [b], operatore)
    with pytest.raises(OperazioneNonConsentita, match="già state assegnate"):
        servizi.registra_ddt("11", dt.date(2026, 6, 1), [a, b], operatore)
    servizi.registra_ddt("10", dt.date(2027, 1, 5), [b], operatore)  # stesso numero, anno diverso: ok
    assert DDT.objects.count() == 2


@pytest.mark.parametrize("numero", ["", "  ", "a b", "x" * 21, "../x"])
def test_numero_ddt_non_valido(in_spedizione, operatore, numero):
    c = servizi.consegna(operatore, ordini_ids=[in_spedizione.pk])
    with pytest.raises(OperazioneNonConsentita):
        servizi.registra_ddt(numero, dt.date(2026, 1, 1), list(c.righe.values_list("pk", flat=True)), operatore)


# ----------------------------------------------------------------------------------------- etichette
def test_etichette_xlsx(in_spedizione):
    from openpyxl import load_workbook

    in_spedizione.appendice = "T1"
    in_spedizione.save()
    ws = load_workbook(io.BytesIO(servizi.etichette_xlsx([in_spedizione]))).active
    riga = [c.value for c in ws[1]]
    assert riga[0] == "4300" and riga[3] == "In spedizione" and riga[5] == "T1" and riga[7] == 2026
    assert riga[8] == "0003659" and riga[10] == "40" and riga[11] == "202604004300"
    assert ws.cell(1, 3).number_format == "DD/MM/YYYY"


# ----------------------------------------------------------------------------------------- viste
def test_viste_consegna(client, in_spedizione, operatore, cliente):
    client.force_login(cliente)
    assert client.get("/consegne/").status_code == 403
    client.force_login(operatore)
    assert b"40.0004300-2026" in client.get("/consegne/").content
    assert client.get("/consegne/etichette/?serie=40")["Content-Type"].startswith("application/vnd.openxml")
    assert client.get("/consegne/consegna/").status_code == 405
    r = client.post("/consegne/consegna/", {"serie": ["40"]})
    c = Consegna.objects.get()
    assert r["Location"] == f"/consegne/{c.pk}/"
    assert client.get(f"/consegne/{c.pk}/file/").content == bytes(c.file_contenuto)
    r = client.post(
        "/consegne/ddt/", {"numero": "55", "data": "2026-04-02", "riga": list(c.righe.values_list("pk", flat=True))}
    )
    assert r.status_code == 302 and DDT.objects.get().numero == "55"
    assert b"55/2026" in client.get("/consegne/ddt/storico/").content
