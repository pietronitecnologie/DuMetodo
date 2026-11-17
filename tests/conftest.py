import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth.models import Group, User

from dumetodo.core.ruoli import Ruolo
from dumetodo.ordini.models import Ordine, RigaOrdine


def _utente(username: str, *ruoli: Ruolo) -> User:
    u = User.objects.create_user(username, f"{username}@example.com", "password-di-test", first_name=username.title())
    for r in ruoli:
        u.groups.add(Group.objects.get_or_create(name=r.value)[0])
    return u


@pytest.fixture
def operatore(db) -> User:
    return _utente("operatore", Ruolo.OPERATORE)


@pytest.fixture
def caporeparto(db) -> User:
    return _utente("caporeparto", Ruolo.CAPOREPARTO)


@pytest.fixture
def amministratore(db) -> User:
    return _utente("admin", Ruolo.AMMINISTRATORE)


@pytest.fixture
def cliente(db) -> User:
    return _utente("cliente", Ruolo.CLIENTE)


@pytest.fixture
def ordine(db) -> Ordine:
    o = Ordine.objects.create(
        serie="00040", numero="0004300", appendice="", anno=2026, data_scadenza=dt.date(2026, 4, 2)
    )
    RigaOrdine.objects.create(
        ordine=o, n_riga="0000001", articolo="ART-A", qta=Decimal(10), prezzo_unitario=Decimal("2.5")
    )
    RigaOrdine.objects.create(
        ordine=o, n_riga="0000002", articolo="ART-B", qta=Decimal(5), prezzo_unitario=Decimal("1")
    )
    return o


@pytest.fixture
def configura_email(db):
    from dumetodo.configurazione import servizi as config

    config.scrivi_molti(
        {
            "email.mittente": "metodo@example.com",
            "email.fornitore": "fornitore@example.com",
            "email.cliente": "cliente1@example.com, cliente2@example.com",
        }
    )


@pytest.fixture(autouse=True)
def _pulisci_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()
