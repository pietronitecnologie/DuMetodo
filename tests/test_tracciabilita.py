"""Archivio delle tracce (sola lettura). Nuove registrazioni, controlli qualità e foto: modulo Tracciatura."""

import datetime as dt
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from openpyxl import load_workbook

from dumetodo.configurazione import servizi as config
from dumetodo.core.permessi import F, ha_permesso
from dumetodo.ordini.models import Ordine
from dumetodo.tracciabilita import servizi
from dumetodo.tracciabilita.models import Elemento, Traccia
from dumetodo.tracciabilita.servizi import OperazioneNonConsentita

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200


def _xlsx(response) -> list[tuple]:
    assert response["Content-Type"].startswith("application/vnd.openxmlformats")
    ws = load_workbook(io.BytesIO(response.content)).active
    return list(ws.iter_rows(min_row=2, values_only=True))


def _traccia(elemento=None, ordine=None, giorni_fa=0, chiave="X", valore="", **kw):
    data = timezone.make_aware(
        dt.datetime.combine(timezone.localdate() - dt.timedelta(days=giorni_fa), dt.time(23, 30))
    )
    return Traccia.objects.create(elemento=elemento, ordine=ordine, data=data, chiave=chiave, valore=valore, **kw)


# =========================================================================== ordini
def test_risolvi_ordine_per_chiave_completa(ordine):
    altro_anno = Ordine.objects.create(serie="00040", numero="0004300", appendice="", anno=2025)
    con_appendice = Ordine.objects.create(serie="00040", numero="0004300", appendice="T1", anno=2026)
    assert servizi.risolvi_ordine("40.0004300-2026") == ordine
    assert servizi.risolvi_ordine("00040.0004300-2026") == ordine
    assert servizi.risolvi_ordine("00040.4300-2026") == ordine
    assert servizi.risolvi_ordine("00040.0004300.0.2026") == ordine  # chiave ticket, appendice "0"
    assert servizi.risolvi_ordine("40.0004300-2025") == altro_anno
    assert servizi.risolvi_ordine("40.0004300-T1-2026") == con_appendice
    assert servizi.risolvi_ordine("00040.0004300.T1-2026") == con_appendice
    with pytest.raises(OperazioneNonConsentita, match="non trovato"):
        servizi.risolvi_ordine("40.0004300-2024")
    with pytest.raises(OperazioneNonConsentita, match="non valido"):
        servizi.risolvi_ordine("4300")  # niente ricerche "contains" sul solo numero


# =========================================================================== archivio
def test_lista_tracce_mostra_di_default_gli_ultimi_giorni(client, operatore):
    _traccia(Elemento.objects.create(codice="RECENTE"), giorni_fa=1)
    _traccia(Elemento.objects.create(codice="VECCHIA"), giorni_fa=30)
    client.force_login(operatore)
    pagina = client.get(reverse("tracciabilita:tracce")).content.decode()
    assert "RECENTE" in pagina and "VECCHIA" not in pagina and "Nuova tracciatura" in pagina
    righe = _xlsx(client.get(reverse("tracciabilita:tracce"), {"formato": "xlsx"}))
    assert [r[1] for r in righe] == ["RECENTE"]
    assert "VECCHIA" in client.get(reverse("tracciabilita:tracce"), {"dal": "", "al": ""}).content.decode()


def test_archivio_in_sola_lettura():
    for nome in ("traccia_nuova", "lotto_busta", "file_carica", "file_lista", "qualita", "qualita_nuovo"):
        with pytest.raises(NoReverseMatch):
            reverse(f"tracciabilita:{nome}")


def test_operatore_registra_con_le_tracciature(operatore):
    # il vecchio permesso di registrare tracce non esiste più: si registra con il modulo Tracciatura
    assert ha_permesso(operatore, F.TRACCIABILITA_CONSULTA) and ha_permesso(operatore, F.TRACCIATURA_REGISTRA)
    assert not hasattr(F, "TRACCIABILITA_REGISTRA") and not hasattr(F, "QUALITA_TEST")


def test_stampa_barcode_escape_e_parametro_obbligatorio(client, operatore):
    client.force_login(operatore)
    assert client.get(reverse("tracciabilita:stampa")).status_code == 400
    r = client.get(reverse("tracciabilita:stampa"), {"barcode": 'A1,"><script>alert(1)</script>'})
    pagina = r.content.decode()
    assert r.status_code == 200 and "<script>alert" not in pagina and "&quot;&gt;&lt;script&gt;" in pagina


def test_cliente_vede_solo_le_tracce_dei_suoi_ordini(client, cliente, ordine):
    gen = _traccia(Elemento.objects.create(codice="SEGRETO"))
    con_ordine = _traccia(ordine=ordine, chiave="LOTTO", valore="L-1")
    client.force_login(cliente)
    assert client.get(gen.get_absolute_url()).status_code == 404
    assert client.get(con_ordine.get_absolute_url()).status_code == 200
    pagina = client.get(reverse("tracciabilita:tracce"), {"dal": "", "al": ""}).content.decode()
    assert "L-1" in pagina and "SEGRETO" not in pagina


def test_pannello_dell_ordine_solo_se_ci_sono_tracce(client, operatore, ordine):
    client.force_login(operatore)
    pagina = client.get(ordine.get_absolute_url()).content.decode()
    assert reverse("tracciabilita:pannello_ordine", args=[ordine.pk]) in pagina
    url = reverse("tracciabilita:pannello_ordine", args=[ordine.pk])
    assert "Tracce (archivio)" not in client.get(url).content.decode()
    _traccia(ordine=ordine, chiave="LOTTO", valore="LOTTO-9")
    pannello = client.get(url).content.decode()
    assert "Tracce (archivio)" in pannello and "LOTTO-9" in pannello and "Salva lotto" not in pannello


# =========================================================================== file (usati dalle tracciature)
def test_valida_file_contenuto_e_dimensione(db):
    finto = SimpleUploadedFile("foto.jpg", b"<html><script>alert(1)</script>", "image/jpeg")
    with pytest.raises(OperazioneNonConsentita, match="non ammesso"):
        servizi.valida_file(finto)
    assert servizi.valida_file(SimpleUploadedFile("ok.jpg", JPEG))[0] == "image/jpeg"
    config.scrivi("tracciabilita.dimensione_massima_file_mb", 1)
    config.invalida_cache()
    grande = SimpleUploadedFile("g.jpg", JPEG + b"\x00" * (1024 * 1024), "image/jpeg")
    with pytest.raises(OperazioneNonConsentita, match="dimensione massima"):
        servizi.valida_file(grande)
