import json

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from dumetodo.client import menu
from dumetodo.configurazione import servizi as config
from dumetodo.core.permessi import F, permessi_di

UA_CLIENT = "Mozilla/5.0 AppleWebKit DuMetodoClient/0.1.0"


def test_menu_predefinito_valido():
    assert menu.valida(json.dumps(menu.PREDEFINITO)) == menu.PREDEFINITO
    assert {"id", "codice", "ordine", "riga", "articolo", "scorporo"} >= menu.segnaposti(menu.PREDEFINITO)


@pytest.mark.parametrize(
    "testo,messaggio",
    [
        ("{", "JSON non valido"),
        ("[]", "oggetto"),
        ('{"Tipo!": []}', "Tipo di menu"),
        ('{"ordine": [{"etichetta": "x", "azione": "rm -rf"}]}', "azione"),
        ('{"ordine": [{"etichetta": "x", "azione": "naviga", "url": "https://evil.example"}]}', "percorso interno"),
        ('{"ordine": [{"etichetta": "x", "azione": "naviga", "url": "//evil.example"}]}', "percorso interno"),
        ('{"ordine": [{"etichetta": "x", "azione": "copia"}]}', "testo"),
        ('{"ordine": [{"etichetta": "x", "azione": "ricarica", "permesso": "inventato"}]}', "permesso sconosciuto"),
        ('{"ordine": [{"etichetta": "x", "azione": "ricarica", "script": "alert(1)"}]}', "campi sconosciuti"),
        ('{"ordine": [{"azione": "ricarica"}]}', "etichetta"),
    ],
)
def test_menu_non_validi(testo, messaggio):
    with pytest.raises(menu.MenuNonValido, match=messaggio):
        menu.valida(testo)


@pytest.mark.django_db
def test_parametro_validato_in_configurazione():
    with pytest.raises(ValidationError, match="percorso interno"):
        config.scrivi("client.menu", '{"ordine": [{"etichetta": "x", "azione": "naviga", "url": "http://x"}]}')
    config.scrivi("client.menu", '{"pagina": [{"etichetta": "Ricarica", "azione": "ricarica"}]}')


@pytest.mark.django_db
def test_menu_filtrato_per_permessi(client):
    u = User.objects.create_user("x")
    u.user_permissions.set(permessi_di([F.ORDINI_VEDERE]))
    client.force_login(u)
    dati = client.get("/client/configurazione.json").json()["menu"]
    assert [v["etichetta"] for v in dati["riga"]] == ["Copia articolo"]  # azioni sulle righe nascoste
    assert all(not v.get("separatore") for v in dati["riga"][:1])
    assert "permesso" not in json.dumps(dati)
    assert "Cambia ordine…" not in [v.get("etichetta") for v in dati["ordine"]]
    u.user_permissions.add(*permessi_di([F.ORDINI_RIGHE, F.UTILITA_CAMBIA_ORDINE]))
    dati = client.get("/client/configurazione.json").json()["menu"]
    assert "Evadi" in [v.get("etichetta") for v in dati["riga"]]
    assert "Cambia ordine…" in [v.get("etichetta") for v in dati["ordine"]]


@pytest.mark.django_db
def test_endpoint_richiede_login(client):
    assert client.get("/client/configurazione.json").status_code == 302


def test_script_solo_nel_client(client, ordine, operatore):
    client.force_login(operatore)
    normale = client.get(f"/ordini/{ordine.pk}/").content.decode()
    assert "client/client.js" not in normale and 'data-dm-menu="riga"' in normale
    desktop = client.get(f"/ordini/{ordine.pk}/", HTTP_USER_AGENT=UA_CLIENT).content.decode()
    assert "client/client.js" in desktop


# =========================================================================== scorciatoie da tastiera
def test_scorciatoie_predefinite_filtrate_per_permessi(client, db):
    from django.contrib.auth.models import User

    from dumetodo.core.permessi import F, permessi_di

    u = User.objects.create_user("x")
    u.user_permissions.set(permessi_di([F.ORDINI_VEDERE]))
    client.force_login(u)
    tasti = {s["tasto"]: s for s in client.get("/client/configurazione.json").json()["scorciatoie"]}
    assert tasti["F1"]["azione"] == "aiuto" and tasti["F3"]["url"] == "/ordini/"
    assert "F6" not in tasti  # consegna: permesso mancante
    assert "permesso" not in tasti["F3"] and "CTRL+SHIFT+N" in tasti


@pytest.mark.parametrize(
    ("voci", "errore"),
    [
        ('[{"tasto": "Q", "etichetta": "x", "azione": "aiuto"}]', "Ctrl o Alt"),
        ('[{"tasto": "Ctrl+T", "etichetta": "x", "azione": "aiuto"}]', "già usato dal client"),
        (
            '[{"tasto": "F2", "etichetta": "x", "azione": "aiuto"},'
            ' {"tasto": "f2", "etichetta": "y", "azione": "aiuto"}]',
            "due volte",
        ),
        ('[{"tasto": "F5", "etichetta": "x", "azione": "naviga", "url": "https://altro.it"}]', "percorso interno"),
        ('[{"tasto": "F5", "etichetta": "x", "azione": "esegui"}]', "non valida"),
        ('{"F5": 1}', "elenco"),
    ],
)
def test_scorciatoie_non_valide(voci, errore):
    from dumetodo.client.scorciatoie import ScorciatoieNonValide, valida

    with pytest.raises(ScorciatoieNonValide, match=errore):
        valida(voci)


def test_normalizza_tasto():
    from dumetodo.client.scorciatoie import normalizza_tasto

    assert normalizza_tasto("shift + ctrl + k") == "CTRL+SHIFT+K"
    assert normalizza_tasto("alt+3") == "ALT+3"


# =========================================================================== aggiornamenti del client
@pytest.fixture
def media(settings, tmp_path):
    settings.STORAGES = {**settings.STORAGES, "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}}
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def _exe(contenuto=b"MZ" + b"\x00" * 500):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile("dumetodo-client.exe", contenuto, content_type="application/octet-stream")


def test_caricamento_e_proposta_aggiornamento(client, db, media):
    import hashlib

    from django.contrib.auth.models import User

    from dumetodo.core.permessi import F, permessi_di

    admin = User.objects.create_user("a")
    admin.user_permissions.set(permessi_di([F.CONFIGURAZIONE]))
    client.force_login(admin)
    assert client.get("/client/aggiornamento.json").json() == {"aggiornamento": None}
    contenuto = b"MZ" + b"x" * 1000
    r = client.post("/client/versioni/", {"versione": "0.3.0", "file": _exe(contenuto), "note": "Novità"})
    assert r.status_code == 302
    client.post("/client/versioni/", {"versione": "0.10.0", "file": _exe()})
    client.post("/client/versioni/", {"versione": "0.9.1", "file": _exe()})
    a = client.get("/client/aggiornamento.json").json()["aggiornamento"]
    assert a["versione"] == "0.10.0"  # confronto numerico, non alfabetico
    # ritirando la 0.10.0 si torna a proporre la più alta rimasta
    from dumetodo.client.models import VersioneClient

    v10 = VersioneClient.objects.get(versione="0.10.0")
    client.post("/client/versioni/", {"azione": "disattiva", "pk": v10.pk})
    a = client.get("/client/aggiornamento.json").json()["aggiornamento"]
    assert a["versione"] == "0.9.1"
    v3 = VersioneClient.objects.get(versione="0.3.0")
    assert v3.sha256 == hashlib.sha256(contenuto).hexdigest()
    r = client.get(f"/client/scarica/{v3.pk}/")
    assert b"".join(r.streaming_content) == contenuto and "dumetodo-client-0.3.0.exe" in r["Content-Disposition"]
    assert client.get(f"/client/scarica/{v10.pk}/").status_code == 404  # ritirata


@pytest.mark.parametrize(
    ("versione", "contenuto", "errore"),
    [
        ("tre", b"MZ...", "Versione non valida"),
        ("0.4.0", b"\x7fELF...", "non è un programma Windows"),
    ],
)
def test_caricamento_rifiutato(client, db, media, versione, contenuto, errore):
    from django.contrib.auth.models import User

    from dumetodo.core.permessi import F, permessi_di

    u = User.objects.create_user("a")
    u.user_permissions.set(permessi_di([F.CONFIGURAZIONE]))
    client.force_login(u)
    r = client.post("/client/versioni/", {"versione": versione, "file": _exe(contenuto)}, follow=True)
    assert errore in r.content.decode()


def test_eliminazione_libera_la_versione(client, db, media, django_capture_on_commit_callbacks):
    from django.contrib.auth.models import User

    from dumetodo.client.models import VersioneClient
    from dumetodo.core.permessi import F, permessi_di

    u = User.objects.create_user("a")
    u.user_permissions.set(permessi_di([F.CONFIGURAZIONE]))
    client.force_login(u)
    client.post("/client/versioni/", {"versione": "0.3.0", "file": _exe()})
    v = VersioneClient.objects.get(versione="0.3.0")
    percorso = media / v.file.name
    assert percorso.exists()
    from dumetodo.client import aggiornamenti

    with pytest.raises(aggiornamenti.AggiornamentoNonValido, match="già stata caricata"):
        aggiornamenti.carica(u, "0.3.0", _exe())
    with django_capture_on_commit_callbacks(execute=True):
        r = client.post("/client/versioni/", {"azione": "elimina", "pk": v.pk}, follow=True)
    assert "Client 0.3.0 eliminato" in r.content.decode()
    assert not VersioneClient.objects.exists() and not percorso.exists()
    client.post("/client/versioni/", {"versione": "0.3.0", "file": _exe()})
    assert VersioneClient.objects.filter(versione="0.3.0").exists()
    # senza permesso: niente
    client.force_login(User.objects.create_user("x"))
    v = VersioneClient.objects.get()
    assert client.post("/client/versioni/", {"azione": "elimina", "pk": v.pk}).status_code == 403
    assert VersioneClient.objects.exists()


def test_aggiornamenti_solo_con_permesso(client, db):
    from django.contrib.auth.models import User

    client.force_login(User.objects.create_user("x"))
    assert client.get("/client/versioni/").status_code == 403
    assert client.get("/client/aggiornamento.json").status_code == 200  # ogni client collegato lo legge
