import pytest
from django.contrib.auth.models import Permission, User

from dumetodo.core.permessi import FUNZIONI, F, applica_profilo, codici_profilo, ha_permesso, is_cliente


@pytest.fixture
def gestore(db):
    u = User.objects.create_user("gestore", password="password-lunga-123")
    applica_profilo(u, "Amministratore")
    return User.objects.get(pk=u.pk)


def test_permessi_sincronizzati(db):
    codici = set(Permission.objects.filter(content_type__model="funzionalita").values_list("codename", flat=True))
    assert codici == {f.codice for f in FUNZIONI}


def test_profili(db):
    u = User.objects.create_user("x")
    applica_profilo(u, "Operatore")
    u = User.objects.get(pk=u.pk)
    assert ha_permesso(u, F.CONSEGNA) and not ha_permesso(u, F.ORDINI_ANNULLA) and not is_cliente(u)
    applica_profilo(u, "Cliente")
    u = User.objects.get(pk=u.pk)
    assert is_cliente(u) and ha_permesso(u, F.ORDINI_VEDERE) and not ha_permesso(u, F.CONSEGNA)
    assert codici_profilo("Amministratore") == {f.codice for f in FUNZIONI}


def test_utente_disattivo_non_ha_permessi(db):
    u = User.objects.create_user("x", is_active=False)
    applica_profilo(u, "Amministratore")
    assert not ha_permesso(User.objects.get(pk=u.pk), F.ORDINI_VEDERE)


def test_pagina_utenti(client, gestore):
    client.force_login(gestore)
    assert client.get("/utenti/").status_code == 200
    r = client.post(
        "/utenti/nuovo/",
        {
            "username": "mario",
            "password": "una-password-sicura-9",
            "attivo": "on",
            "permesso": [F.ORDINI_VEDERE, F.REPORT_DASHBOARD, "inventato"],
        },
    )
    mario = User.objects.get(username="mario")
    assert r["Location"] == f"/utenti/{mario.pk}/"
    assert {p.codename for p in mario.user_permissions.all()} == {F.ORDINI_VEDERE, F.REPORT_DASHBOARD}
    # profilo + identità Cliente
    client.post(
        f"/utenti/{mario.pk}/", {"username": "mario", "attivo": "on", "azione": "profilo", "profilo": "Cliente"}
    )
    assert is_cliente(User.objects.get(pk=mario.pk))
    # password debole rifiutata, utente duplicato rifiutato
    r = client.post("/utenti/nuovo/", {"username": "luigi", "password": "123", "attivo": "on"}, follow=True)
    assert not User.objects.filter(username="luigi").exists() and b"password" in r.content.lower()
    client.post("/utenti/nuovo/", {"username": "MARIO", "password": "una-password-sicura-9", "attivo": "on"})
    assert User.objects.filter(username__iexact="mario").count() == 1


def test_non_ci_si_chiude_fuori(client, gestore):
    client.force_login(gestore)
    client.post(f"/utenti/{gestore.pk}/", {"username": "gestore", "attivo": "on", "permesso": [F.ORDINI_VEDERE]})
    assert ha_permesso(User.objects.get(pk=gestore.pk), F.UTENTI)
    client.post(f"/utenti/{gestore.pk}/", {"username": "gestore", "permesso": [F.UTENTI]})
    assert User.objects.get(pk=gestore.pk).is_active


def test_senza_permesso_403(client, db):
    u = User.objects.create_user("x")
    client.force_login(u)
    assert client.get("/utenti/").status_code == 403


def test_pagina_iniziale_secondo_i_permessi(client, db):
    from dumetodo.core.permessi import permessi_di

    u = User.objects.create_user("x")
    client.force_login(u)
    assert client.get("/").status_code == 403
    u.user_permissions.set(permessi_di([F.REPORT_PREZZI, F.REPORT_DASHBOARD]))
    assert client.get("/")["Location"] == "/report/"
    u.user_permissions.add(*permessi_di([F.ORDINI_VEDERE]))
    assert client.get("/")["Location"] == "/ordini/"


def test_nuove_funzioni_agli_amministratori_completi(db):
    from dumetodo.core import permessi
    from dumetodo.core.permessi import Funzione, Sezione, sincronizza_permessi

    admin = User.objects.create_user("a")
    applica_profilo(admin, "Amministratore")
    operatore = User.objects.create_user("o")
    applica_profilo(operatore, "Operatore")
    nuova = Funzione("funzione_di_prova", Sezione.UTILITA, "Prova")
    permessi.FUNZIONI.append(nuova)
    try:
        sincronizza_permessi()
        assert ha_permesso(User.objects.get(pk=admin.pk), "funzione_di_prova")
        assert not ha_permesso(User.objects.get(pk=operatore.pk), "funzione_di_prova")
    finally:
        permessi.FUNZIONI.remove(nuova)
        sincronizza_permessi()
