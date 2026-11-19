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
