import pytest
from django.contrib.auth.models import User

from metodo.chat import servizi
from metodo.chat.models import MessaggioChat
from metodo.core.permessi import F, permessi_di


def _utente(nome, chat=True):
    u = User.objects.create_user(nome, first_name=nome.capitalize())
    if chat:
        u.user_permissions.set(permessi_di([F.CHAT]))
    return User.objects.get(pk=u.pk)


@pytest.fixture
def anna(db):
    return _utente("anna")


@pytest.fixture
def bruno(db):
    return _utente("bruno")


def test_messaggi_salvati_e_non_letti(anna, bruno):
    servizi.invia(anna, bruno.pk, "Ciao, l'ordine 4300 è pronto?")
    servizi.invia(anna, bruno.pk, "Serve per domani")
    assert MessaggioChat.objects.count() == 2  # salvati sul server
    s = servizi.stato(bruno)
    assert s["non_letti"] == 2 and s["conversazioni"][0]["nome"] == "Anna"
    assert [m["mio"] for m in s["conversazioni"][0]["messaggi"]] == [False, False]
    servizi.segna_letti(bruno, anna.pk)
    assert servizi.stato(bruno)["non_letti"] == 0
    assert all(m["letto"] for m in servizi.stato(anna)["conversazioni"][0]["messaggi"])


def test_chiudere_nasconde_ma_non_cancella(anna, bruno):
    servizi.invia(anna, bruno.pk, "uno")
    servizi.chiudi(bruno, anna.pk)
    assert servizi.stato(bruno)["conversazioni"] == []
    assert MessaggioChat.objects.count() == 1 and servizi.stato(anna)["conversazioni"]  # per anna resta
    servizi.invia(anna, bruno.pk, "due")  # un nuovo messaggio la riapre, con tutto lo storico
    conv = servizi.stato(bruno)["conversazioni"][0]
    assert [m["testo"] for m in conv["messaggi"]] == ["uno", "due"]


def test_regole(anna, bruno, db):
    senza = _utente("carlo", chat=False)
    with pytest.raises(servizi.ChatNonConsentita, match="vuoto"):
        servizi.invia(anna, bruno.pk, "   ")
    with pytest.raises(servizi.ChatNonConsentita, match="te stesso"):
        servizi.invia(anna, anna.pk, "x")
    with pytest.raises(servizi.ChatNonConsentita, match="non può ricevere"):
        servizi.invia(anna, senza.pk, "x")
    with pytest.raises(servizi.ChatNonConsentita, match="permesso"):
        servizi.invia(senza, anna.pk, "x")
    with pytest.raises(servizi.ChatNonConsentita, match="troppo lungo"):
        servizi.invia(anna, bruno.pk, "x" * 2001)


def test_presenza(anna, bruno):
    assert not any(p["collegato"] for p in servizi.stato(anna)["persone"])
    servizi.stato(bruno)  # bruno ha il pannello aperto
    persone = {p["nome"]: p for p in servizi.stato(anna)["persone"]}
    assert persone["Bruno"]["collegato"] and "Anna" not in persone


def test_viste_chat(client, anna, bruno):
    client.force_login(anna)
    assert client.get("/chat/").status_code == 200
    r = client.post("/chat/invia/", {"a": bruno.pk, "testo": "<script>alert(1)</script>"})
    assert r.status_code == 200
    assert client.post("/chat/invia/", {"a": bruno.pk, "testo": ""}).status_code == 400
    client.force_login(bruno)
    s = client.get("/chat/stato/").json()
    assert s["conversazioni"][0]["messaggi"][0]["testo"] == "<script>alert(1)</script>"  # testo, mai HTML
    client.post("/chat/letti/", {"con": anna.pk})
    assert client.get("/chat/stato/").json()["non_letti"] == 0
    client.force_login(User.objects.create_user("senza"))
    assert client.get("/chat/").status_code == 403
