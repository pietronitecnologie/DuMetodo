"""Pagina di errore CSRF in italiano, con il motivo per l'amministratore (nessun dato riservato)."""

from django.test import Client


def test_pagina_errore_csrf_con_motivo(db):
    client = Client(enforce_csrf_checks=True)
    r = client.post("/accesso/", {"username": "x", "password": "y"})
    assert r.status_code == 403
    testo = r.content.decode()
    assert "controllo di sicurezza" in testo and "Per l'amministratore:" in testo and "CSRF" in testo
