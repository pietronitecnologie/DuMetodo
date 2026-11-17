import pytest

from dumetodo.importazione.models import FileImportato
from dumetodo.ordini.models import StatoOrdine

from .test_importazione import file_txt, riga


def test_anonimo_rediretto_al_login(client, db):
    for url in ("/ordini/", "/importazione/", "/configurazione/"):
        r = client.get(url)
        assert r.status_code == 302 and "/accesso/" in r["Location"]


def test_lista_e_dettaglio_ordini(client, ordine, cliente, operatore):
    client.force_login(cliente)
    r = client.get("/ordini/")
    assert r.status_code == 200 and b"40.0004300-2026" in r.content
    r = client.get(f"/ordini/{ordine.pk}/")
    assert r.status_code == 200 and b"Prezzo" not in r.content  # i prezzi li vede solo l'amministratore
    r = client.get("/ordini/?articolo=ART-A", HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and b"<html" not in r.content  # HTMX: solo la tabella


def test_cliente_non_puo_modificare(client, ordine, cliente):
    client.force_login(cliente)
    assert client.post(f"/ordini/{ordine.pk}/stato/", {"stato": "LAV"}).status_code == 403
    assert client.get("/importazione/").status_code == 403


def test_cambio_stato_solo_post(client, ordine, operatore):
    client.force_login(operatore)
    assert client.get(f"/ordini/{ordine.pk}/stato/?stato=LAV").status_code == 405
    client.post(f"/ordini/{ordine.pk}/stato/", {"stato": "LAV"})
    ordine.refresh_from_db()
    assert ordine.stato == StatoOrdine.IN_LAVORAZIONE


def test_caricamento_file_da_interfaccia(client, operatore):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client.force_login(operatore)
    f = SimpleUploadedFile("OCL_1.TXT", file_txt(riga()))
    r = client.post("/importazione/", {"file": [f]})
    assert r.status_code == 200 and b"Importato" in r.content
    file = FileImportato.objects.get()
    assert client.get(f"/importazione/file/{file.pk}/").status_code == 200
    assert client.get(f"/importazione/file/{file.pk}/scarica/").content == file_txt(riga())
    r = client.post("/importazione/", {"file": [SimpleUploadedFile("x.exe", b"MZ")]})
    assert b"solo file .txt" in r.content


@pytest.mark.parametrize("torna", ["https://evil.example.com/", "//evil.example.com/"])
def test_segna_gestito_non_permette_redirect_esterni(client, operatore, torna):
    from dumetodo.importazione import servizi

    client.force_login(operatore)
    f = servizi.importa_caricamento("x.txt", b"rotto", operatore)
    p = f.problemi.get()
    r = client.post(f"/importazione/problemi/{p.pk}/gestito/", {"torna": torna})
    assert r["Location"] == "/importazione/problemi/"
