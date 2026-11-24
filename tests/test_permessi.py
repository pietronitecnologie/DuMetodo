import pytest
from django.contrib.auth.models import Permission, User

from dumetodo.core.permessi import FUNZIONI, F, applica_profilo, codici_profilo, ha_permesso, is_cliente
from dumetodo.core.utenti import PERMESSI_AREA_UTENTI as PERMESSI_AREA


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
    # "tutte" le funzioni, tranne le riservate (Gestionale): quelle solo con il profilo Amministrazione
    assert codici_profilo("Amministratore") == {f.codice for f in FUNZIONI if not f.riservata}
    assert {f.codice for f in FUNZIONI if f.riservata} <= codici_profilo("Amministrazione")


def test_utente_disattivo_non_ha_permessi(db):
    u = User.objects.create_user("x", is_active=False)
    applica_profilo(u, "Amministratore")
    assert not ha_permesso(User.objects.get(pk=u.pk), F.ORDINI_VEDERE)


def test_pagina_utenti(client, gestore):
    client.force_login(gestore)
    assert client.get("/utenti/").status_code == 200
    r = client.post(
        "/utenti/nuovo/",
        {"azione": "dati", "username": "mario", "password": "una-password-sicura-9", "profilo": "Operatore"},
    )
    mario = User.objects.get(username="mario")
    assert r["Location"] == f"/utenti/{mario.pk}/"
    assert ha_permesso(mario, F.CONSEGNA) and mario.profilo_dumetodo.deve_cambiare_password
    # permessi singoli (quelli inventati ignorati)
    client.post(f"/utenti/{mario.pk}/", {"azione": "permessi", "permesso": [F.ORDINI_VEDERE, F.REPORT_DASHBOARD, "x"]})
    assert {p.codename for p in User.objects.get(pk=mario.pk).user_permissions.all()} == {
        F.ORDINI_VEDERE,
        F.REPORT_DASHBOARD,
    }
    # profilo Cliente
    client.post(f"/utenti/{mario.pk}/", {"azione": "profilo", "profilo": "Cliente"})
    assert is_cliente(User.objects.get(pk=mario.pk))
    # password debole rifiutata, utente duplicato rifiutato
    r = client.post("/utenti/nuovo/", {"username": "luigi", "password": "123"}, follow=True)
    assert not User.objects.filter(username="luigi").exists() and b"password" in r.content.lower()
    client.post("/utenti/nuovo/", {"username": "MARIO", "password": "una-password-sicura-9"})
    assert User.objects.filter(username__iexact="mario").count() == 1
    # storico
    from dumetodo.core.models import EventoUtente

    azioni = set(EventoUtente.objects.filter(utente=mario).values_list("azione", flat=True))
    assert {"CRE", "PRO", "PER"} <= azioni


def test_non_ci_si_chiude_fuori(client, gestore):
    client.force_login(gestore)
    r = client.post(f"/utenti/{gestore.pk}/", {"azione": "permessi", "permesso": [F.ORDINI_VEDERE]}, follow=True)
    assert ha_permesso(User.objects.get(pk=gestore.pk), F.UTENTI_PERMESSI) and "toglierti" in r.content.decode()
    client.post(f"/utenti/{gestore.pk}/", {"azione": "dati", "username": "gestore"})  # senza "attivo"
    assert User.objects.get(pk=gestore.pk).is_active


def _utente(nome, *codici):
    from dumetodo.core.permessi import permessi_di

    u = User.objects.create_user(nome, password="password-lunga-123")
    u.user_permissions.set(permessi_di(codici))
    return User.objects.get(pk=u.pk)


def test_nessuna_auto_promozione(client, db):
    """Chi gestisce gli utenti non può dare (né darsi) permessi che non ha."""
    capo = _utente("capo", F.UTENTI_VEDERE, F.UTENTI_PERMESSI, F.ORDINI_VEDERE, F.CONSEGNA)
    op = _utente("op", F.ORDINI_VEDERE)
    client.force_login(capo)
    client.post(f"/utenti/{op.pk}/", {"azione": "permessi", "permesso": [F.CONSEGNA, F.CONFIGURAZIONE]})
    assert {p.codename for p in User.objects.get(pk=op.pk).user_permissions.all()} == {F.CONSEGNA}
    miei = [F.UTENTI_VEDERE, F.UTENTI_PERMESSI, F.ORDINI_VEDERE, F.CONSEGNA]
    client.post(f"/utenti/{capo.pk}/", {"azione": "permessi", "permesso": [*miei, F.CONFIGURAZIONE, F.UTENTI_GESTIRE]})
    assert not ha_permesso(User.objects.get(pk=capo.pk), F.CONFIGURAZIONE)
    # profilo con permessi che non ha: rifiutato
    r = client.post(f"/utenti/{op.pk}/", {"azione": "profilo", "profilo": "Amministratore"}, follow=True)
    t = r.content.decode()
    assert "permessi che non hai" in t


def test_non_si_tocca_chi_ha_piu_permessi(client, db):
    """Chi reimposta password non può prendere il controllo di un amministratore."""
    capo = _utente("capo", F.UTENTI_VEDERE, F.UTENTI_PASSWORD, F.UTENTI_GESTIRE, F.UTENTI_PERMESSI)
    admin = _utente("adm", *[f.codice for f in FUNZIONI])
    root = User.objects.create_superuser("root", password="password-lunga-123")
    client.force_login(capo)
    for bersaglio in (admin, root):
        client.post(f"/utenti/{bersaglio.pk}/", {"azione": "password_temporanea"})
        client.post(f"/utenti/{bersaglio.pk}/", {"azione": "dati", "username": bersaglio.username})
        u = User.objects.get(pk=bersaglio.pk)
        assert u.check_password("password-lunga-123") and u.is_active
    assert "puoi vederlo ma non modificarlo" in client.get(f"/utenti/{admin.pk}/").content.decode()
    # superuser solo da superuser
    client.post(f"/utenti/{capo.pk}/", {"azione": "superuser", "valore": "1"})
    assert not User.objects.get(pk=capo.pk).is_superuser


def test_password_temporanea_e_cambio_obbligatorio(client, db):
    capo = _utente("capo", F.UTENTI_VEDERE, F.UTENTI_PASSWORD, F.ORDINI_VEDERE)
    op = _utente("op", F.ORDINI_VEDERE)
    client.force_login(capo)
    r = client.post(f"/utenti/{op.pk}/", {"azione": "password_temporanea"})
    import re

    temporanea = re.search(r'id="pwd-temp">([^<]+)<', r.content.decode()).group(1)
    assert r["Cache-Control"] == "no-store" and User.objects.get(pk=op.pk).check_password(temporanea)
    # l'utente deve cambiarla prima di fare altro
    client.force_login(User.objects.get(pk=op.pk))
    assert client.get("/ordini/")["Location"] == "/utenti/password/"
    # htmx dalla pagina del cambio password (annunci di base.html): nessun rimando, niente ricarica infinita
    hx = {"HTTP_HX_REQUEST": "true", "HTTP_HX_CURRENT_URL": "http://server/utenti/password/"}
    r = client.get("/utilita/annunci/attivi/", **hx)
    assert r.status_code == 204 and "HX-Redirect" not in r
    hx["HTTP_HX_CURRENT_URL"] = "http://server/ordini/"
    assert client.get("/utilita/annunci/attivi/", **hx)["HX-Redirect"] == "/utenti/password/"
    # le richieste di dati (client, chat) ricevono un errore, non la pagina HTML
    assert client.get("/client/configurazione.json", HTTP_ACCEPT="application/json").status_code == 403
    r = client.post("/utenti/password/", {"attuale": temporanea, "nuova": "x", "conferma": "x"}, follow=True)
    assert "troppo corta" in r.content.decode() or "short" in r.content.decode()
    nuova = "una-nuova-password-99"
    r = client.post("/utenti/password/", {"attuale": temporanea, "nuova": nuova, "conferma": nuova})
    assert r.status_code == 302 and client.get("/ordini/").status_code == 200
    assert User.objects.get(pk=op.pk).check_password(nuova)


def test_permessi_separati_dell_area_utenti(client, db):
    solo_vedere = _utente("v", F.UTENTI_VEDERE)
    op = _utente("op", F.ORDINI_VEDERE)
    client.force_login(solo_vedere)
    assert client.get("/utenti/").status_code == 200 and client.get(f"/utenti/{op.pk}/").status_code == 200
    assert client.get("/utenti/nuovo/").status_code == 403
    client.post(f"/utenti/{op.pk}/", {"azione": "dati", "username": "cambiato", "attivo": "on"})
    client.post(f"/utenti/{op.pk}/", {"azione": "password_temporanea"})
    u = User.objects.get(pk=op.pk)
    assert u.username == "op" and u.check_password("password-lunga-123")


def test_vecchio_permesso_utenti_diviso(db):
    from django.contrib.auth.models import Permission
    from django.contrib.contenttypes.models import ContentType

    from dumetodo.core.models import Funzionalita
    from dumetodo.core.permessi import sincronizza_permessi

    ct = ContentType.objects.get_for_model(Funzionalita, for_concrete_model=False)
    vecchio = Permission.objects.create(content_type=ct, codename="utenti", name="Utenti e permessi")
    u = User.objects.create_user("x")
    u.user_permissions.add(vecchio)
    sincronizza_permessi()
    u = User.objects.get(pk=u.pk)
    assert all(ha_permesso(u, c) for c in PERMESSI_AREA)
    assert not Permission.objects.filter(codename="utenti").exists()


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


@pytest.mark.django_db
def test_crea_amministratore_idempotente():
    from io import StringIO

    from django.contrib.auth.models import User
    from django.core.management import call_command

    out = StringIO()
    call_command("crea_amministratore", stdout=out)
    password = out.getvalue().split("password: ")[1].split()[0]
    admin = User.objects.get(username="admin")
    assert admin.is_superuser and admin.check_password(password)

    out = StringIO()
    call_command("crea_amministratore", stdout=out)
    assert "esiste già" in out.getvalue()
    admin.refresh_from_db()
    assert admin.check_password(password)


# =========================================================================== anagrafiche senza admin di Django
def test_reparti_dall_app(client, db):
    from dumetodo.core.models import Reparto

    u = _utente("r", F.REPARTI)
    client.force_login(u)
    assert client.get("/reparti/").status_code == 200
    dati = {"codice": "gk01-new", "nome": "Nuovo", "soglia_ore_settimanali": "80", "attivo": "on"}
    r = client.post("/reparti/nuovo/", dati)
    assert r.status_code == 302
    rep = Reparto.objects.get(codice="GK01-NEW")
    client.post(f"/reparti/{rep.pk}/", {"codice": "GK01-NEW", "nome": "Nuovo", "soglia_ore_settimanali": "-5"})
    assert Reparto.objects.get(pk=rep.pk).soglia_ore_settimanali == 80  # negativo rifiutato
    client.force_login(_utente("x", F.ORDINI_VEDERE))
    assert client.get("/reparti/").status_code == 403


def test_nuovo_articolo_dall_app(client, db):
    from dumetodo.anagrafica.models import Articolo

    client.force_login(_utente("m", F.MAGAZZINO_ANAGRAFICHE))
    r = client.post(
        "/magazzino/articoli/crea/",
        {"codice": "art-99", "descrizione": "Prova", "um": "EA", "prezzo": "12,50"},
        follow=True,
    )
    a = Articolo.objects.get(codice__iexact="art-99")
    assert a.descrizione == "Prova" and str(a.prezzo_riferimento) == "12.5000" and not a.nuovo
    r = client.post("/magazzino/articoli/crea/", {"codice": "ART-99", "um": "EA"}, follow=True)
    assert "esiste già" in r.content.decode()


def test_utente_di_sistema_nascosto(client, db):
    from dumetodo.migrazione.tracciabilita import MODELLI
    from dumetodo.tracciatura import da_qualita

    sistema = da_qualita.utente_sistema(MODELLI)
    admin = User.objects.create_superuser("root", password="password-di-test")
    client.force_login(admin)
    pagina = client.get("/utenti/?stato=tutti").content.decode()
    assert "root" in pagina and da_qualita.UTENTE_SISTEMA not in pagina
    assert client.get(f"/utenti/{sistema.pk}/").status_code == 404
    assert not sistema.is_active and not sistema.has_usable_password()
