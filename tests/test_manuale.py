from django.contrib.auth.models import User
from django.urls import reverse

from dumetodo.core import manuale
from dumetodo.core.menu import MENU


def test_ogni_capitolo_collegato_esiste():
    ancore = manuale.ancore()
    mancanti = {a for _, a in manuale.CAPITOLI if a not in ancore}
    assert not mancanti, f"Ancore senza capitolo nel manuale: {mancanti}"


def test_ogni_voce_del_menu_ha_un_capitolo():
    senza = [v.url for s in MENU for v in s.voci if not v.solo_staff and not manuale.capitolo_per(v.url)]
    assert not senza, senza


def test_pagina_e_file_md(client, db):
    assert client.get("/manuale/").status_code == 302  # serve l'accesso
    client.force_login(User.objects.create_user("x"))
    r = client.get("/manuale/")
    assert r.status_code == 200 and 'id="ordini"' in r.content.decode() and "Scarica (.md)" in r.content.decode()
    r = client.get("/manuale/manuale-utente.md")
    assert r["Content-Type"].startswith("text/markdown") and r.content.decode().startswith("# Manuale utente")


def test_pulsante_aiuto_porta_al_capitolo_in_altra_scheda(client, db):
    from dumetodo.core.permessi import F, permessi_di

    u = User.objects.create_user("x")
    u.user_permissions.set(permessi_di([F.ORDINI_VEDERE]))
    client.force_login(u)
    html = client.get(reverse("ordini:lista")).content.decode()
    assert 'class="dm-aiuto" href="/manuale/#ordini" target="_blank"' in html
