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
    r = client.get("/ordini/")  # senza filtri la pagina parte vuota (gli ordini sono migliaia)
    assert r.status_code == 200 and b"40.0004300-2026" not in r.content and b"Imposta almeno un filtro" in r.content
    r = client.get("/ordini/?q=4300")
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


@pytest.mark.django_db
def test_dati_demo_si_caricano_e_le_pagine_rispondono(client):
    from io import StringIO

    from django.contrib.auth.models import User
    from django.core.management import call_command
    from django.urls import reverse

    from dumetodo.ordini.models import Ordine

    call_command("crea_amministratore", stdout=StringIO())
    User.objects.get(username="admin").profilo_dumetodo.__class__.objects.update(deve_cambiare_password=False)
    out = StringIO()
    call_command("carica_dati_demo", stdout=out, stderr=StringIO())
    assert "Dati di prova caricati" in out.getvalue()
    assert Ordine.objects.count() == 48 and Ordine.objects.filter(stato="CON").exists()
    from dumetodo.consegne.models import DDT
    from dumetodo.gestionale.models import Fattura

    assert DDT.objects.filter(origine="DUM").exists() and Fattura.objects.filter(stato="BOZ").exists()
    assert "amministrazione" in out.getvalue()
    from dumetodo.timbrature.models import Lavoratore, Timbratura

    assert Lavoratore.objects.count() == 6 and Timbratura.objects.exists()
    from dumetodo.tracciatura.models import Modello, Tracciatura

    # 3 modelli di esempio + «Lotto e busta» + il controllo qualità «Pulsantiere»
    assert Modello.objects.count() == 5 and Tracciatura.objects.exists()
    client.force_login(User.objects.get(username="admin"))
    for nome in [
        "ordini:lista",
        "report:dashboard",
        "pianificazione:agenda",
        "consegne:pagina",
        "tracciabilita:tracce",
    ]:
        assert client.get(reverse(nome)).status_code == 200, nome
    with pytest.raises(Exception, match="già ordini"):
        call_command("carica_dati_demo", stdout=StringIO())
