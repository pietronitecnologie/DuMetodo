"""Ogni pagina richiede il login e un permesso: nessuna vista resta scoperta per errore."""

import re

import pytest
from django.contrib.auth.models import User
from django.urls import URLPattern, URLResolver, get_resolver

PUBBLICHE = {
    "login",
    "logout",
    "utilita:annunci_attivi",  # frammento vuoto per chi non è del Fornitore
    "configurazione:logo",  # il logo compare anche nella pagina di accesso (404 se non caricato)
    # Postazioni di timbratura: senza login, le protegge il link segreto (404 senza token valido).
    "timbrature:postazione",
    "timbrature:postazione_identifica",
    "timbrature:postazione_timbra",
    "chat:pannello",  # prima dell'accesso: pagina d'attesa senza dati (vedi test sotto); dopo, chiede il permesso
}
# Solo login: servono a ogni utente collegato e restituiscono dati già filtrati per i suoi permessi.
SOLO_LOGIN = {
    "client:configurazione",
    "client:aggiornamento",
    "client:scarica",
    "utenti:mia_password",
    "manuale",
    "manuale_md",
    "timbrature:timbra_qui",  # apre la postazione associata al PC (o torna alla home)
    "indietro",  # pulsante «indietro»: rimanda solo a pagine di questo sito, ognuna con i suoi permessi
}


def _rotte(resolver: URLResolver = None, prefisso: str = "", ns: str = ""):  # type: ignore[assignment]
    resolver = resolver or get_resolver()
    for p in resolver.url_patterns:
        if isinstance(p, URLResolver):
            if p.namespace == "admin":
                continue
            yield from _rotte(p, prefisso + str(p.pattern), f"{ns}{p.namespace}:" if p.namespace else ns)
        elif isinstance(p, URLPattern) and p.name:
            percorso = prefisso + str(p.pattern)
            percorso = re.sub(r"<int:\w+>", "1", percorso)
            percorso = re.sub(r"<(?:str:|slug:)?\w+>", "x", percorso)
            yield f"{ns}{p.name}", "/" + percorso.lstrip("^").rstrip("$")


ROTTE = sorted({(nome, url) for nome, url in _rotte() if nome not in PUBBLICHE})


@pytest.mark.parametrize(("nome", "url"), ROTTE, ids=[n for n, _ in ROTTE])
def test_anonimo_e_utente_senza_permessi(client, db, nome, url):
    r = client.get(url)
    assert r.status_code in (302, 405), f"{nome}: {r.status_code}"
    if r.status_code == 302:
        assert "/accesso/" in r["Location"], nome
    if nome in SOLO_LOGIN:
        return
    client.force_login(User.objects.create_user("nessuno"))
    r = client.get(url)
    assert r.status_code in (403, 405), f"{nome}: {r.status_code}"


def test_le_rotte_sono_state_trovate():
    assert len(ROTTE) > 80


def test_pannello_chat_senza_dati_prima_dell_accesso_e_con_permesso_dopo(client, db):
    r = client.get("/chat/")
    assert r.status_code == 200 and "dopo l'accesso" in r.content.decode() and len(r.content) < 600
    client.force_login(User.objects.create_user("nessuno"))
    assert client.get("/chat/").status_code == 403
