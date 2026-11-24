"""Funzioni rimaste della tracciabilità (ordini come testo, controllo dei file): il resto è nelle Tracciature."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from dumetodo.configurazione import servizi as config
from dumetodo.core.permessi import F, ha_permesso
from dumetodo.ordini.models import Ordine
from dumetodo.tracciabilita import servizi
from dumetodo.tracciabilita.servizi import OperazioneNonConsentita

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200


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


# =========================================================================== archivio eliminato
def test_archivio_delle_tracce_eliminato(client, operatore):
    client.force_login(operatore)
    assert client.get("/tracciabilita/tracce/").status_code == 404
    assert not hasattr(F, "TRACCIABILITA_CONSULTA") and not hasattr(F, "TRACCIABILITA_REGISTRA")
    assert ha_permesso(operatore, F.TRACCIATURA_REGISTRA)


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
