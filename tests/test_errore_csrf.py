"""Pagina di errore CSRF in italiano, con il motivo per l'amministratore (nessun dato riservato)."""

from django.test import Client


def test_pagina_errore_csrf_con_motivo(db):
    client = Client(enforce_csrf_checks=True)
    r = client.post("/accesso/", {"username": "x", "password": "y"})
    assert r.status_code == 403
    testo = r.content.decode()
    assert "controllo di sicurezza" in testo and "Per l'amministratore:" in testo and "CSRF" in testo


def test_cookie_con_nomi_propri(db):
    """Un'altra applicazione Django sullo stesso dominio (csrftoken / sessionid) non disturba il login."""
    from django.contrib.auth.models import User

    User.objects.create_user("mario", password="Una-Password-Lunga-1")
    client = Client(enforce_csrf_checks=True)
    pagina = client.get("/accesso/")
    assert "metodo_csrftoken" in pagina.cookies and "csrftoken" not in pagina.cookies
    token = pagina.context["csrf_token"]
    client.cookies["csrftoken"] = "cookie-di-un-altra-applicazione-xxxxxxxxxxxxxxx"  # es. il Metodo precedente
    client.cookies["sessionid"] = "sessione-di-un-altra-applicazione"
    r = client.post(
        "/accesso/", {"username": "mario", "password": "Una-Password-Lunga-1", "csrfmiddlewaretoken": token}
    )
    assert r.status_code == 302 and "metodo_sessione" in r.cookies


def test_pannello_chat_prima_dell_accesso_non_crea_il_cookie_csrf(db):
    """Il client desktop carica la chat nascosta insieme alla scheda visibile: prima dell'accesso la chat non deve
    mostrare la pagina di accesso, che creerebbe un secondo cookie CSRF e farebbe fallire il login."""
    client = Client(enforce_csrf_checks=True)
    r = client.get("/chat/")
    assert r.status_code == 200 and 'http-equiv="refresh"' in r.content.decode()
    assert "metodo_csrftoken" not in r.cookies and "metodo_sessione" not in r.cookies
    # il login nella scheda visibile funziona anche con la chat aperta in parallelo
    from django.contrib.auth.models import User

    User.objects.create_user("anna", password="Una-Password-Lunga-1")
    pagina = client.get("/accesso/")
    client.get("/chat/")
    r = client.post(
        "/accesso/",
        {"username": "anna", "password": "Una-Password-Lunga-1", "csrfmiddlewaretoken": pagina.context["csrf_token"]},
    )
    assert r.status_code == 302
