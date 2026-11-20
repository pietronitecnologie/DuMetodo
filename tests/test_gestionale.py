"""Sezione Gestionale: permessi riservati, DDT di legge, fatture differite FatturaPA, note di credito."""

import datetime as dt
import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from dumetodo.configurazione import servizi as config
from dumetodo.consegne import servizi as consegne
from dumetodo.consegne.models import DDT
from dumetodo.core.permessi import F, applica_profilo, codici_profilo, ha_permesso, sincronizza_permessi
from dumetodo.gestionale import fatturapa, servizi
from dumetodo.gestionale.models import Fattura, StatoFattura
from dumetodo.ordini.models import Ordine, RigaOrdine, StatoOrdine
from dumetodo.ordini.servizi import OperazioneNonConsentita

NS = {"p": fatturapa.NS}
OGGI = timezone.localdate()


def _utente(nome: str, profilo: str) -> User:
    u = User.objects.create_user(nome, password="password-di-test")
    applica_profilo(u, profilo)
    return User.objects.get(pk=u.pk)


@pytest.fixture
def ufficio(db) -> User:
    return _utente("ufficio", "Amministrazione")


@pytest.fixture
def dati_fiscali(db):
    config.scrivi_molti(
        {
            "gestionale.fornitore_denominazione": "Fornitore Prova S.r.l.",
            "gestionale.fornitore_partita_iva": "01234567897",
            "gestionale.fornitore_indirizzo": "Via Roma 1",
            "gestionale.fornitore_cap": "20100",
            "gestionale.fornitore_comune": "Milano",
            "gestionale.fornitore_provincia": "MI",
            "gestionale.fornitore_rea_ufficio": "MI",
            "gestionale.fornitore_rea_numero": "123456",
            "gestionale.cliente_denominazione": "Cliente & Figli S.p.A.",
            "gestionale.cliente_partita_iva": "IT09876543217",
            "gestionale.cliente_indirizzo": "Via Torino 2",
            "gestionale.cliente_cap": "27100",
            "gestionale.cliente_comune": "Pavia",
            "gestionale.cliente_provincia": "PV",
            "gestionale.cliente_codice_destinatario": "abc1234",
            "gestionale.pagamento_iban": "IT60 X054 2811 1010 0000 0123 456",
        }
    )
    config.invalida_cache()


def _consegna(ufficio: User, serie: str = "00040", numero: str = "0004300", **righe) -> list[int]:
    """Ordine in spedizione con le righe indicate (articolo=(qta, prezzo)), consegnato: id delle righe consegnate."""
    o = Ordine.objects.create(
        serie=serie, numero=numero, anno=OGGI.year, data_scadenza=OGGI, stato=StatoOrdine.IN_SPEDIZIONE
    )
    for n, (art, (qta, prezzo)) in enumerate(righe.items(), start=1):
        RigaOrdine.objects.create(ordine=o, n_riga=f"{n:07}", articolo=art, qta=Decimal(qta), prezzo_unitario=prezzo)
    c = consegne.consegna(ufficio, ordini_ids=[o.pk])
    return list(c.righe.values_list("pk", flat=True))


def _campi_ddt(**extra):
    return {**servizi.valori_predefiniti_ddt(), "colli": 2, "peso_kg": Decimal("12.5"), **extra}


# ----------------------------------------------------------------------------------------- permessi
def test_gestionale_solo_al_profilo_amministrazione(client, db, ufficio):
    admin = _utente("it", "Amministratore")
    root = User.objects.create_superuser("root", password="password-di-test")
    assert not ({F.GESTIONALE_VEDERE, F.GESTIONALE_FATTURE} & codici_profilo("Amministratore"))
    for u in (admin, root):
        assert not ha_permesso(u, F.GESTIONALE_VEDERE)
        client.force_login(u)
        assert client.get("/gestionale/").status_code == 403
        assert "/gestionale/" not in client.get("/configurazione/").content.decode()
    client.force_login(ufficio)
    pagina = client.get("/gestionale/")
    assert pagina.status_code == 200 and "Emetti DDT" in pagina.content.decode()
    # una funzione riservata nuova non arriva agli amministratori completi
    sincronizza_permessi()
    assert not ha_permesso(User.objects.get(pk=admin.pk), F.GESTIONALE_DDT)
    # ma il superuser può assegnarla esplicitamente
    root.user_permissions.add(*User.objects.get(pk=ufficio.pk).user_permissions.filter(codename=F.GESTIONALE_VEDERE))
    assert ha_permesso(User.objects.get(pk=root.pk), F.GESTIONALE_VEDERE)


def test_dati_fiscali_fuori_da_configurazione(client, db, ufficio, dati_fiscali):
    admin = _utente("it", "Amministratore")
    client.force_login(admin)
    html = client.get("/configurazione/").content.decode()
    assert "Dati fiscali del Fornitore" not in html and "Partita IVA" not in html
    assert "gestionale." not in client.get("/configurazione/storico/").content.decode()
    client.force_login(ufficio)
    r = client.post(
        "/gestionale/impostazioni/",
        {
            "gruppo": "GESTIONALE_DOCUMENTI",
            "gestionale_documenti-gestionale.aliquota_iva": "10",
            "gestionale_documenti-gestionale.esigibilita_iva": "I",
            "gestionale_documenti-gestionale.pagamento_modalita": "MP12",
            "gestionale_documenti-gestionale.pagamento_giorni": "30",
            "gestionale_documenti-gestionale.natura_iva": "",
            "gestionale_documenti-gestionale.ddt_causale": "Vendita",
            "gestionale_documenti-gestionale.ddt_trasporto_a_cura": "Vettore",
            "gestionale_documenti-gestionale.ddt_porto": "Assegnato",
            "gestionale_documenti-gestionale.formato_numero_fattura": "{numero}/{anno}",
        },
    )
    assert r.status_code == 302
    config.invalida_cache()
    assert (
        config.leggi("gestionale.aliquota_iva") == Decimal("10")
        and config.leggi("gestionale.pagamento_modalita") == "MP12"
    )
    r = client.post(
        "/gestionale/impostazioni/",
        {"gruppo": "GESTIONALE_DOCUMENTI", "gestionale_documenti-gestionale.pagamento_modalita": "MP99"},
    )
    assert "da correggere" in r.content.decode()


# ---------------------------------------------------------------------------------------------- XML
def _doc(**extra) -> fatturapa.Documento:
    base = dict(
        tipo="TD24",
        numero="7",
        data=dt.date(2026, 11, 30),
        cedente=fatturapa.Soggetto(
            "Fornitore S.r.l.",
            "01234567897",
            indirizzo="Via Roma 1",
            cap="20100",
            comune="Milano",
            provincia="MI",
            email="info@f.it",
        ),
        cessionario=fatturapa.Soggetto(
            "Cliente S.p.A.",
            "09876543217",
            indirizzo="Via Torino 2",
            cap="27100",
            comune="Pavia",
            provincia="PV",
            codice_destinatario="ABC1234",
        ),
        linee=[
            fatturapa.Linea(
                1,
                "Pulsantiera <A> & co",
                Decimal("3"),
                Decimal("10.333"),
                Decimal("22"),
                codice="ART-1",
                unita_misura="Pezzi",
                sconto_unitario=Decimal("0.333"),
            ),
            fatturapa.Linea(2, "Cavo", Decimal("2.5"), Decimal("4"), Decimal("22")),
        ],
        progressivo_invio=fatturapa.progressivo_invio(1),
        ddt=[fatturapa.RiferimentoDDT("12", dt.date(2026, 11, 3), [1, 2])],
        pagamento=fatturapa.Pagamento("MP05", dt.date(2027, 1, 31), "IT60X0542811101000000123456", "Banca"),
    )
    base.update(extra)
    return fatturapa.Documento(**base)


def _figli(el: ET.Element) -> list[str]:
    return [c.tag for c in el]


def test_xml_fattura_differita_struttura_e_importi():
    xml = fatturapa.genera(_doc())
    radice = ET.fromstring(xml)
    assert radice.tag == f"{{{fatturapa.NS}}}FatturaElettronica" and radice.get("versione") == "FPR12"
    h = radice.find("FatturaElettronicaHeader")
    assert _figli(h) == ["DatiTrasmissione", "CedentePrestatore", "CessionarioCommittente"]
    assert _figli(h.find("DatiTrasmissione")) == [
        "IdTrasmittente",
        "ProgressivoInvio",
        "FormatoTrasmissione",
        "CodiceDestinatario",
        "ContattiTrasmittente",
    ]
    assert _figli(h.find("CedentePrestatore")) == ["DatiAnagrafici", "Sede", "Contatti"]
    assert h.findtext("CedentePrestatore/DatiAnagrafici/IdFiscaleIVA/IdCodice") == "01234567897"
    b = radice.find("FatturaElettronicaBody")
    assert _figli(b) == ["DatiGenerali", "DatiBeniServizi", "DatiPagamento"]
    dgd = b.find("DatiGenerali/DatiGeneraliDocumento")
    assert _figli(dgd) == ["TipoDocumento", "Divisa", "Data", "Numero", "ImportoTotaleDocumento"]
    assert dgd.findtext("TipoDocumento") == "TD24" and dgd.findtext("Data") == "2026-11-30"
    ddt = b.find("DatiGenerali/DatiDDT")
    assert ddt.findtext("NumeroDDT") == "12" and [e.text for e in ddt.findall("RiferimentoNumeroLinea")] == ["1", "2"]
    l1 = b.find("DatiBeniServizi/DettaglioLinee")
    assert _figli(l1) == [
        "NumeroLinea",
        "CodiceArticolo",
        "Descrizione",
        "Quantita",
        "UnitaMisura",
        "PrezzoUnitario",
        "ScontoMaggiorazione",
        "PrezzoTotale",
        "AliquotaIVA",
    ]
    assert l1.findtext("Descrizione") == "Pulsantiera <A> & co"  # escape corretto nel file
    assert l1.findtext("Quantita") == "3.00" and l1.findtext("PrezzoUnitario") == "10.333"
    assert l1.findtext("PrezzoTotale") == "30.00" and l1.findtext("AliquotaIVA") == "22.00"
    # 30 + 10 = 40 imponibile, 8.80 IVA
    rp = b.find("DatiBeniServizi/DatiRiepilogo")
    assert (rp.findtext("ImponibileImporto"), rp.findtext("Imposta"), rp.findtext("EsigibilitaIVA")) == (
        "40.00",
        "8.80",
        "I",
    )
    assert dgd.findtext("ImportoTotaleDocumento") == "48.80"
    dp = b.find("DatiPagamento/DettaglioPagamento")
    assert _figli(dp) == [
        "Beneficiario",
        "ModalitaPagamento",
        "DataRiferimentoTerminiPagamento",
        "GiorniTerminiPagamento",
        "DataScadenzaPagamento",
        "ImportoPagamento",
        "IstitutoFinanziario",
        "IBAN",
    ]
    assert dp.findtext("GiorniTerminiPagamento") == "62" and dp.findtext("ImportoPagamento") == "48.80"
    assert fatturapa.nome_file(_doc()) == f"IT01234567897_{fatturapa.progressivo_invio(1)}.xml"
    assert b"&amp;" in xml and xml.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')


def test_xml_nota_di_credito_esente_con_bollo_e_pec():
    ces = fatturapa.Soggetto(
        "Privato S.r.l.",
        "09876543217",
        indirizzo="Via 1",
        cap="27100",
        comune="Pavia",
        codice_destinatario="0000000",
        pec="fatture@pec.it",
    )
    doc = _doc(
        tipo="TD04",
        cessionario=ces,
        ddt=[],
        fattura_collegata=("5", dt.date(2026, 10, 31)),
        riferimento_normativo="Art. 8 c.1 lett. c DPR 633/72",
        linee=[fatturapa.Linea(1, "Reso", Decimal("10"), Decimal("10"), Decimal("0"), natura="N3.5")],
        pagamento=None,
    )
    radice = ET.fromstring(fatturapa.genera(doc))
    assert radice.findtext("FatturaElettronicaHeader/DatiTrasmissione/PECDestinatario") == "fatture@pec.it"
    g = radice.find("FatturaElettronicaBody/DatiGenerali")
    assert _figli(g) == ["DatiGeneraliDocumento", "DatiFattureCollegate"]
    assert g.findtext("DatiFattureCollegate/IdDocumento") == "5"
    assert g.findtext("DatiGeneraliDocumento/DatiBollo/ImportoBollo") == "2.00"  # 100 € senza IVA, N3.5
    rp = radice.find("FatturaElettronicaBody/DatiBeniServizi/DatiRiepilogo")
    assert _figli(rp) == ["AliquotaIVA", "Natura", "ImponibileImporto", "Imposta", "RiferimentoNormativo"]
    # esportazione (N3.1): niente bollo
    doc.linee[0].natura = "N3.1"
    assert ET.fromstring(fatturapa.genera(doc)).find(".//DatiBollo") is None


def test_controlli_che_lo_sdi_farebbe():
    assert fatturapa.controlla(_doc()) == []
    errori = " ".join(
        fatturapa.controlla(
            _doc(
                tipo="TD24",
                ddt=[],
                cessionario=fatturapa.Soggetto("X", "123", codice_destinatario="12"),
                linee=[fatturapa.Linea(1, "a", Decimal(1), Decimal(1), Decimal(0))],
            )
        )
    )
    for frase in ("partita IVA non valida", "codice destinatario", "natura", "DDT", "CAP mancante"):
        assert frase in errori
    with pytest.raises(fatturapa.FatturaNonValida):
        fatturapa.genera(_doc(linee=[]))
    assert fatturapa.importo8(Decimal("2")) == "2.00" and fatturapa.importo8(Decimal("1.123456789")) == "1.12345679"
    assert fatturapa.solo_codice("IT 01234567897") == "01234567897"
    assert len({fatturapa.progressivo_invio(n) for n in range(1, 3000)}) == 2999


# ------------------------------------------------------------------------------------------ flusso
def test_ddt_di_legge_numerati_e_file_tecnico(db, ufficio, dati_fiscali, django_capture_on_commit_callbacks):
    config.scrivi("gestionale.cliente_destinazione", "Magazzino, via Po 3, Pavia")
    config.scrivi("email.file_tecnico", "dmg@example.com")
    config.invalida_cache()
    a = _consegna(ufficio, **{"ART-A": (10, Decimal("2.5")), "ART-B": (5, Decimal("1"))})
    with django_capture_on_commit_callbacks(execute=True):
        d1 = servizi.emetti_ddt(ufficio, a[:1], OGGI, _campi_ddt())
    assert (d1.numero, d1.progressivo, d1.emesso_da_dumetodo) == ("1", 1, True)
    assert d1.file_nome and d1.email is not None  # stesso file tecnico di Spedizioni → DDT
    assert d1.documento["destinatario"]["denominazione"] == "Cliente & Figli S.p.A."
    assert d1.documento["destinazione"].startswith("Magazzino") and d1.documento["righe"][0]["codice"] == "ART-A"
    # un numero già usato da un DDT registrato a mano viene saltato
    b = _consegna(ufficio, numero="0004301", **{"ART-C": (1, Decimal("3"))})
    consegne.registra_ddt("2", OGGI, b, ufficio)
    d3 = servizi.emetti_ddt(ufficio, a[1:], OGGI, _campi_ddt(trasporto_a_cura="Vettore", vettore="Corriere SpA"))
    assert d3.numero == "3"
    with pytest.raises(OperazioneNonConsentita, match="vettore"):
        servizi.emetti_ddt(ufficio, [], OGGI, _campi_ddt(trasporto_a_cura="Vettore", vettore=""))
    with pytest.raises(OperazioneNonConsentita, match="futuro"):
        servizi.emetti_ddt(ufficio, a, OGGI + dt.timedelta(days=1), _campi_ddt())
    # annullamento: numero consumato, righe di nuovo da emettere
    servizi.annulla_ddt(ufficio, d1.pk, "colli sbagliati")
    assert servizi.righe_da_emettere().filter(pk=a[0]).exists()
    d4 = servizi.emetti_ddt(ufficio, a[:1], OGGI, _campi_ddt())
    assert d4.numero == "4"
    with pytest.raises(OperazioneNonConsentita, match="emessi da DuMetodo"):
        servizi.annulla_ddt(ufficio, DDT.objects.get(numero="2").pk, "x")


def test_ddt_richiede_dati_fiscali_e_permesso(db, ufficio, operatore):
    a = _consegna(ufficio, **{"ART-A": (1, Decimal("1"))})
    with pytest.raises(OperazioneNonConsentita, match="dati fiscali"):
        servizi.emetti_ddt(ufficio, a, OGGI, _campi_ddt())
    with pytest.raises(OperazioneNonConsentita, match="permessi"):
        servizi.emetti_ddt(operatore, a, OGGI, _campi_ddt())


def test_fattura_differita_emissione_e_nota_di_credito(db, ufficio, dati_fiscali):
    a = _consegna(ufficio, **{"ART-A": (10, Decimal("2.5")), "ART-B": (5, Decimal("1")), "ART-S": (2, Decimal("9"))})
    RigaOrdine.objects.filter(articolo="ART-S").update(scorporata=True)  # consegnata ma non si fattura
    RigaOrdine.objects.filter(articolo="ART-A").update(sconto=Decimal("0.5"))
    d1 = servizi.emetti_ddt(ufficio, a[:2], OGGI, _campi_ddt())
    d2 = servizi.emetti_ddt(ufficio, a[2:], OGGI, _campi_ddt())
    assert list(servizi.ddt_da_fatturare()) == [d1]  # d2 ha solo una riga scorporata
    f = servizi.crea_fattura_differita(ufficio, [d1.pk], OGGI)
    assert f.is_bozza and f.tipo == "TD24" and f.righe.count() == 2
    # 10 × (2.5 − 0.5) + 5 × 1 = 25 imponibile, 22% = 5.50
    assert (f.imponibile, f.imposta, f.totale) == (Decimal("25.00"), Decimal("5.50"), Decimal("30.50"))
    assert not servizi.ddt_da_fatturare().exists()
    with pytest.raises(OperazioneNonConsentita, match="Nessuna riga"):
        servizi.crea_fattura_differita(ufficio, [d2.pk], OGGI)
    f = servizi.emetti_fattura(ufficio, f.pk)
    assert f.stato == StatoFattura.EMESSA and (f.numero, f.progressivo) == ("1", 1)
    assert f.xml_nome == f"IT01234567897_{f.progressivo_invio}.xml"
    radice = ET.fromstring(f.xml.encode())
    assert radice.findtext(".//DatiDDT/NumeroDDT") == d1.numero
    assert radice.findtext(".//CessionarioCommittente//IdCodice") == "09876543217"  # senza IT
    assert radice.findtext(".//CodiceDestinatario") == "ABC1234"
    assert radice.findtext(".//IBAN") == "IT60X0542811101000000123456"
    assert f.cedente["denominazione"] == "Fornitore Prova S.r.l." and f.scadenza.day >= 28  # fine mese
    with pytest.raises(OperazioneNonConsentita, match="già emessa"):
        servizi.emetti_fattura(ufficio, f.pk)
    with pytest.raises(OperazioneNonConsentita, match="nota di credito"):
        servizi.elimina_bozza(ufficio, f.pk)
    with pytest.raises(OperazioneNonConsentita, match="già in una fattura"):
        servizi.annulla_ddt(ufficio, d1.pk, "x")
    # i dati emessi non cambiano se cambia la configurazione
    config.scrivi("gestionale.cliente_denominazione", "Altro nome")
    config.invalida_cache()
    f.refresh_from_db()
    assert f.cessionario["denominazione"] == "Cliente & Figli S.p.A."

    # nota di credito parziale
    nota = servizi.nota_di_credito(ufficio, f.pk)
    assert nota.tipo == "TD04" and nota.fattura_collegata == f and nota.totale == f.totale
    riga_b = nota.righe.get(n_linea=2)
    servizi.riduci_riga_nota(ufficio, nota.righe.get(n_linea=1).pk, Decimal("0"))
    servizi.riduci_riga_nota(ufficio, riga_b.pk, Decimal("2"))
    nota.refresh_from_db()
    assert (nota.imponibile, nota.totale) == (Decimal("2.00"), Decimal("2.44"))
    with pytest.raises(OperazioneNonConsentita, match="Quantità tra 0"):
        servizi.riduci_riga_nota(ufficio, riga_b.pk, Decimal("6"))
    nota = servizi.emetti_fattura(ufficio, nota.pk)
    assert nota.numero == "2"
    radice = ET.fromstring(nota.xml.encode())
    assert radice.findtext(".//TipoDocumento") == "TD04"
    assert radice.findtext(".//DatiFattureCollegate/IdDocumento") == "1"
    assert radice.find(".//DatiDDT") is None
    # lo storno non rimette i DDT da fatturare
    assert not servizi.ddt_da_fatturare().exists()


def test_fattura_righe_senza_prezzo_e_mesi_diversi(db, ufficio, dati_fiscali):
    a = _consegna(ufficio, **{"ART-A": (1, None)})
    d = servizi.emetti_ddt(ufficio, a, OGGI, _campi_ddt())
    with pytest.raises(OperazioneNonConsentita, match="senza prezzo"):
        servizi.crea_fattura_differita(ufficio, [d.pk], OGGI)
    assert not Fattura.objects.exists()  # tutto o niente
    vecchio = DDT.objects.create(numero="X1", anno=OGGI.year, data=OGGI - dt.timedelta(days=40))
    with pytest.raises(OperazioneNonConsentita, match="un solo mese"):
        servizi.crea_fattura_differita(ufficio, [d.pk, vecchio.pk], OGGI)


def test_numerazione_fatture_segue_le_date(db, ufficio, dati_fiscali):
    config.scrivi("gestionale.formato_numero_fattura", "FT{numero}/{anno}")
    config.invalida_cache()
    a = _consegna(ufficio, **{"ART-A": (1, Decimal("1")), "ART-B": (1, Decimal("1"))})
    d1 = servizi.emetti_ddt(ufficio, a[:1], OGGI, _campi_ddt())
    d2 = servizi.emetti_ddt(ufficio, a[1:], OGGI, _campi_ddt())
    f1 = servizi.emetti_fattura(ufficio, servizi.crea_fattura_differita(ufficio, [d1.pk], OGGI).pk)
    assert f1.numero == f"FT1/{OGGI.year}"
    f2 = servizi.crea_fattura_differita(ufficio, [d2.pk], OGGI)
    Fattura.objects.filter(pk=f1.pk).update(data=OGGI + dt.timedelta(days=1))
    with pytest.raises(OperazioneNonConsentita, match="seguire le date"):
        servizi.emetti_fattura(ufficio, f2.pk)


# --------------------------------------------------------------------------------------------- pagine
def test_pagine_del_gestionale(client, db, ufficio, dati_fiscali):
    a = _consegna(ufficio, **{"ART-A": (4, Decimal("2"))})
    client.force_login(ufficio)
    assert "ART-A" in client.get("/gestionale/ddt/emetti/").content.decode()
    r = client.post(
        "/gestionale/ddt/emetti/",
        {"riga": a, "data": OGGI.isoformat(), **{k: v for k, v in _campi_ddt().items() if v is not None}},
    )
    d = DDT.objects.get()
    assert r.status_code == 302 and r["Location"] == f"/gestionale/ddt/{d.pk}/"
    stampa = client.get(f"/gestionale/ddt/{d.pk}/stampa/").content.decode()
    assert "DOCUMENTO DI TRASPORTO" in stampa and "Cliente &amp; Figli" in stampa and "472" in stampa
    assert client.get("/gestionale/ddt/").status_code == 200
    r = client.post("/gestionale/da-fatturare/", {"ddt": [d.pk], "data": OGGI.isoformat()})
    f = Fattura.objects.get()
    assert r["Location"] == f"/gestionale/fatture/{f.pk}/"
    assert "Emetti" in client.get(r["Location"]).content.decode()
    client.post(f"/gestionale/fatture/{f.pk}/", {"azione": "emetti"})
    f.refresh_from_db()
    xml = client.get(f"/gestionale/fatture/{f.pk}/xml/")
    assert xml["Content-Type"].startswith("application/xml") and f.xml_nome in xml["Content-Disposition"]
    assert "Copia di cortesia" in client.get(f"/gestionale/fatture/{f.pk}/stampa/").content.decode()
    client.post(f"/gestionale/fatture/{f.pk}/", {"azione": "sdi", "stato_sdi": "CON", "note_sdi": "id 123"})
    f.refresh_from_db()
    assert f.stato_sdi == "CON"
    assert "1 DDT emessi" in client.get("/gestionale/").content.decode()
    # collegamento da Spedizioni → Consegna e DDT solo per chi ha il permesso
    assert "Gestionale → DDT da emettere" in client.get("/consegne/ddt/").content.decode()
    client.force_login(_utente("op", "Operatore"))
    assert "Gestionale → DDT da emettere" not in client.get("/consegne/ddt/").content.decode()
    assert client.get(f"/gestionale/fatture/{f.pk}/xml/").status_code == 403
