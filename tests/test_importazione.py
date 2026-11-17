import datetime as dt
import io
import zipfile
from decimal import Decimal

import pytest

from dumetodo.configurazione import servizi as config
from dumetodo.importazione import formato_txt, servizi
from dumetodo.importazione.allegati import AllegatoNonValido, estrai_file_ordine
from dumetodo.importazione.models import FileImportato, Livello, OrigineFile, StatoFile
from dumetodo.ordini.models import Ordine, RigaOrdine, StatoOrdine, StatoRiga, StoricoScadenza

INTESTAZIONE = (
    "Periodo riferimento@Serie numerazione@N.ro Ordine@Appendice@N.ro prog. riga@Data registrazione@Data consegna@"
    "Cod. Articolo@Variante@Quantita@Prezzo@Sconto 1@Sconto 2@Sconto 3@Sconto 4@Sconto 5@Sconto unitario@Prezzo finale"
)


def riga(
    n="0000001",
    art="18090054BR",
    qta="2",
    scad="02042024",
    prezzo="0,2596",
    ordine="0004300",
    var="__0",
    anno="2024",
    appendice="0  ",
):
    # Formato reale (docs/analisi-metodo/02-ordini.md §3.1): 18 intestazioni, 19 valori, campi con spazi.
    return (
        f"{anno}@00040@{ordine}@{appendice}@{n}@22032024@{scad}@{art}   @{var}   @{qta}   @@@@@@@@{prezzo}   "
        f"@701 {ordine} 00001636"
    )


def file_txt(*righe: str, codifica="latin-1") -> bytes:
    return ("\r\n".join([INTESTAZIONE, *righe]) + "\r\n").encode(codifica)


def importa(contenuto: bytes, nome="OCL_4300_40.TXT") -> FileImportato:
    return servizi.importa(servizi.registra_file(nome, contenuto, OrigineFile.CARICAMENTO))


# ----------------------------------------------------------------------------- lettura formato
def test_lettura_riga_reale():
    esito = formato_txt.leggi(file_txt(riga()))
    assert not esito.errori and not esito.errore_file
    r = esito.righe[0]
    assert r.chiave == formato_txt.ChiaveOrdine("00040", "0004300", "", 2024)  # appendice "0" normalizzata
    assert r.qta == Decimal(2) and r.prezzo == Decimal("0.2596")
    assert r.data_scadenza == dt.date(2024, 4, 2)
    assert (r.magazzino, r.cliente, r.variante) == ("701", "00001636", "__0")


def test_lettura_robusta_codifica_bom_e_righe_vuote():
    contenuto = b"\xef\xbb\xbf" + file_txt(riga(art="ART\xe0"), codifica="latin-1") + b"\r\n\r\n"
    esito = formato_txt.leggi(contenuto)
    assert esito.righe[0].articolo == "ARTà"
    assert not esito.errori


@pytest.mark.parametrize(
    "difetto,messaggio",
    [
        (riga(qta="abc"), "quantità"),
        (riga(scad="31022024"), "inesistente"),
        (riga(scad=""), "data"),
        ("2024@00040@0004300", "campi"),
    ],
)
def test_righe_sbagliate_producono_errori_con_numero_di_riga(difetto, messaggio):
    esito = formato_txt.leggi(file_txt(riga(n="0000001"), difetto))
    assert len(esito.righe) == 1
    assert esito.errori[0].riga_file == 3 and messaggio in esito.errori[0].messaggio


def test_intestazione_sconosciuta():
    assert "Intestazione" in formato_txt.leggi(b"a@b@c\r\n1@2@3").errore_file
    assert formato_txt.leggi(b"").errore_file


# ----------------------------------------------------------------------------- applicazione
@pytest.mark.django_db
def test_nuovo_ordine_creato():
    f = importa(file_txt(riga(), riga(n="0000002", art="ART-2", qta="5,5")))
    assert f.stato == StatoFile.IMPORTATO and f.ordini_creati == 1 and f.righe_create == 2
    o = Ordine.objects.get()
    assert (o.codice, o.stato, o.cliente) == ("00040.0004300-2024", StatoOrdine.INSERITO, "00001636")
    assert o.righe.get(n_riga="0000002").qta == Decimal("5.5")


@pytest.mark.django_db
def test_file_identico_non_viene_reimportato():
    importa(file_txt(riga()))
    secondo = importa(file_txt(riga()))
    assert secondo.stato == StatoFile.DUPLICATO
    assert RigaOrdine.objects.count() == 1


@pytest.mark.django_db
def test_reimportazione_aggiorna_annulla_e_sostituisce():
    importa(file_txt(riga(n="0000001"), riga(n="0000002", art="B"), riga(n="0000003", art="C")))
    f = importa(
        file_txt(riga(n="0000001", qta="7", scad="10042024"), riga(n="0000002", art="B-NUOVO", scad="12042024"))
    )
    o = Ordine.objects.get()
    attive = {r.n_riga: r for r in o.righe.attive()}
    assert attive["0000001"].qta == 7 and attive["0000001"].data_scadenza == dt.date(2024, 4, 10)
    assert attive["0000002"].articolo == "B-NUOVO"
    assert "0000003" not in attive
    assert o.righe.get(n_riga="0000002", articolo="B").stato == StatoRiga.SOSTITUITA
    assert o.righe.get(n_riga="0000003").stato == StatoRiga.ANNULLATA
    assert (f.righe_aggiornate, f.righe_create, f.righe_annullate) == (1, 1, 1)
    assert o.data_scadenza == dt.date(2024, 4, 10)  # l'ordine prende la scadenza più vicina delle sue righe
    assert StoricoScadenza.objects.filter(ordine=o, riga=None, nuova=dt.date(2024, 4, 10)).exists()
    assert StoricoScadenza.objects.filter(ordine=o, riga__n_riga="0000001", nuova=dt.date(2024, 4, 10)).exists()


@pytest.mark.django_db
def test_righe_in_bolla_non_vengono_mai_toccate():
    importa(file_txt(riga(n="0000001"), riga(n="0000002", art="B")))
    RigaOrdine.objects.filter(n_riga="0000001").update(bolla_emessa=True)
    f = importa(file_txt(riga(n="0000002", art="B", qta="9")))  # la riga 1 sparisce dal file
    r1 = RigaOrdine.objects.get(n_riga="0000001")
    assert r1.stato == StatoRiga.ATTIVA and r1.bolla_emessa
    assert f.stato == StatoFile.CON_AVVISI
    assert f.problemi.filter(livello=Livello.AVVISO, messaggio__contains="già in bolla").exists()


@pytest.mark.django_db
def test_ordine_consegnato_rifiuta_la_reimportazione():
    importa(file_txt(riga()))
    Ordine.objects.update(stato=StatoOrdine.CONSEGNATO)
    f = importa(file_txt(riga(qta="99")))
    assert f.stato == StatoFile.CON_ERRORI and f.ordini_scartati == 1
    assert RigaOrdine.objects.get().qta == 2


@pytest.mark.django_db
def test_riga_illeggibile_blocca_solo_il_suo_ordine_e_non_annulla_nulla():
    importa(file_txt(riga(n="0000001"), riga(n="0000002", art="B"), riga(ordine="0009999")))
    f = importa(file_txt(riga(n="0000001", qta="3"), riga(n="0000002", qta="x"), riga(ordine="0009999", qta="4")))
    assert f.stato == StatoFile.CON_ERRORI
    o1 = Ordine.objects.get(numero="0004300")
    assert o1.righe.attive().count() == 2  # nessuna riga annullata per errore
    assert o1.righe.get(n_riga="0000001").qta == 2  # ordine non applicato
    assert RigaOrdine.objects.get(ordine__numero="0009999").qta == 4  # l'altro ordine sì


@pytest.mark.django_db
def test_riga_senza_ordine_identificabile_blocca_tutto_il_file():
    importa(file_txt(riga()))
    f = importa(file_txt(riga(qta="8"), riga(anno="24")))
    assert f.stato == StatoFile.CON_ERRORI
    assert RigaOrdine.objects.get().qta == 2


@pytest.mark.django_db
def test_reimportazione_riporta_a_inserito_solo_se_configurato():
    importa(file_txt(riga()))
    Ordine.objects.update(stato=StatoOrdine.IN_LAVORAZIONE)
    importa(file_txt(riga(qta="3")))
    assert Ordine.objects.get().stato == StatoOrdine.INSERITO

    config.scrivi("importazione.ripristina_stato_su_reimport", False)
    config.invalida_cache()
    Ordine.objects.update(stato=StatoOrdine.IN_LAVORAZIONE)
    importa(file_txt(riga(qta="4")))
    assert Ordine.objects.get().stato == StatoOrdine.IN_LAVORAZIONE


@pytest.mark.django_db
def test_reimportazione_rispetta_le_divisioni(caporeparto):
    from dumetodo.ordini.servizi import dividi_riga

    importa(file_txt(riga(qta="10")))
    originale = RigaOrdine.objects.get()
    figlia = dividi_riga(originale.pk, "4", caporeparto)
    importa(file_txt(riga(qta="10", prezzo="0,30")))  # stesso totale: le quantità non cambiano
    originale.refresh_from_db()
    assert (originale.qta, figlia.qta) == (6, 4)
    assert originale.prezzo_unitario == Decimal("0.30")
    importa(file_txt(riga(qta="12")))  # totale aumentato: la differenza va sulla riga originale
    originale.refresh_from_db()
    assert originale.qta == 8


@pytest.mark.django_db
def test_errori_notificati_via_email(configura_email):
    from dumetodo.comunicazioni.models import EmailInUscita

    config.scrivi("importazione.notifica_errori", "it@example.com")
    config.invalida_cache()
    importa(b"intestazione sbagliata")
    email = EmailInUscita.objects.get()
    assert email.destinatari == ["it@example.com"] and "Errori" in email.oggetto


# ----------------------------------------------------------------------------- allegati
def _zip(voci: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nome, dati in voci.items():
            z.writestr(nome, dati)
    return buf.getvalue()


def test_zip_estrae_solo_txt_e_neutralizza_i_percorsi():
    z = _zip({"../../etc/OCL_1.TXT": b"a", "doc.pdf": b"pdf", "sub\\..\\OCL_2.txt": b"b"})
    estratti = estrai_file_ordine("../../evil.zip", z, max_byte=1024)
    assert [e.nome for e in estratti] == ["evil.zip/OCL_1.TXT", "evil.zip/OCL_2.txt"]


def test_zip_bomba_rifiutato():
    z = _zip({"grande.txt": b"0" * 10_000})
    with pytest.raises(AllegatoNonValido):
        estrai_file_ordine("a.zip", z, max_byte=1000)


def test_zip_corrotto_e_altri_allegati():
    with pytest.raises(AllegatoNonValido):
        estrai_file_ordine("a.zip", b"non zip", max_byte=1000)
    assert estrai_file_ordine("foto.jpg", b"x", max_byte=1000) == []
