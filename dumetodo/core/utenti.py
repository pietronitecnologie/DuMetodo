"""Gestione utenti e permessi (servizi)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from .permessi import FUNZIONI, GRUPPO_CLIENTE, F, applica_profilo, ha_permesso, permessi_di


class OperazioneUtenti(Exception):
    pass


def permessi_utente(user: User) -> set[str]:
    return set(user.user_permissions.filter(content_type__model="funzionalita").values_list("codename", flat=True))


@transaction.atomic
def salva_utente(
    attore: Any,
    utente: User | None,
    *,
    username: str,
    nome: str,
    cognome: str,
    email: str,
    attivo: bool,
    cliente: bool,
    permessi: Iterable[str],
    password: str = "",
    profilo: str = "",
) -> User:
    if not ha_permesso(attore, F.UTENTI):
        raise OperazioneUtenti("Non hai i permessi per gestire gli utenti.")
    username = username.strip()
    if not username:
        raise OperazioneUtenti("Il nome utente è obbligatorio.")
    if User.objects.filter(username__iexact=username).exclude(pk=getattr(utente, "pk", None)).exists():
        raise OperazioneUtenti(f"Esiste già un utente '{username}'.")
    nuovo = utente is None
    if nuovo and not password:
        raise OperazioneUtenti("Per un nuovo utente la password è obbligatoria.")
    utente = utente or User(username=username)
    if password:
        try:
            validate_password(password, utente)
        except ValidationError as exc:
            raise OperazioneUtenti(" ".join(exc.messages)) from None
        utente.set_password(password)
    validi = {f.codice for f in FUNZIONI}
    scelti = {p for p in permessi if p in validi}
    if utente.pk == attore.pk:
        if not attivo:
            raise OperazioneUtenti("Non puoi disattivare il tuo utente.")
        if F.UTENTI not in scelti and not attore.is_superuser:
            raise OperazioneUtenti("Non puoi toglierti il permesso 'Utenti e permessi'.")
    utente.username, utente.first_name, utente.last_name = username, nome.strip()[:150], cognome.strip()[:150]
    utente.email, utente.is_active = email.strip(), attivo
    utente.save()
    if profilo:
        applica_profilo(utente, profilo)
    else:
        utente.user_permissions.set(permessi_di(scelti))
        gruppo, _ = Group.objects.get_or_create(name=GRUPPO_CLIENTE)
        (utente.groups.add if cliente else utente.groups.remove)(gruppo)
    return utente
