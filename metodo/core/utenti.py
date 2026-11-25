"""Gestione utenti e permessi (servizi). Le viste chiamano SOLO queste funzioni.

Regole di sicurezza (tutte verificate qui, lato server):
- ogni azione ha il suo permesso: vedere, gestire i dati, reimpostare le password, assegnare permessi;
- nessuno può dare permessi che non ha (niente "auto-promozione" a amministratore);
- un utente con più permessi di chi lo modifica (o un superuser) può essere modificato solo da chi ha
  almeno i suoi permessi (o da un superuser): così chi gestisce gli operatori non può prendere il
  controllo di un amministratore cambiandogli la password;
- non ci si può disattivare né togliersi i permessi dell'area utenti da soli;
- il flag superuser lo cambia solo un superuser;
- ogni modifica finisce nello storico (EventoUtente), le password mai.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from typing import Any

from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import EventoUtente, ProfiloUtente
from .permessi import FUNZIONI, GRUPPO_CLIENTE, PROFILI, F, codici_profilo, ha_permesso, is_cliente, permessi_di

Azione = EventoUtente.Azione

PERMESSI_AREA_UTENTI = (F.UTENTI_VEDERE, F.UTENTI_GESTIRE, F.UTENTI_PASSWORD, F.UTENTI_PERMESSI)


class OperazioneUtenti(Exception):
    pass


# --------------------------------------------------------------------------------------------- lettura
def tutti_i_codici() -> set[str]:
    return {f.codice for f in FUNZIONI}


def permessi_utente(user: User) -> set[str]:
    if user.is_superuser:
        return tutti_i_codici()
    return set(user.user_permissions.filter(content_type__model="funzionalita").values_list("codename", flat=True))


def profilo(user: User) -> ProfiloUtente:
    p, _ = ProfiloUtente.objects.get_or_create(utente=user)
    return p


def concedibili(attore: Any) -> set[str]:
    """Permessi che l'attore può assegnare: quelli che ha (tutti, se superuser)."""
    return tutti_i_codici() if attore.is_superuser else permessi_utente(attore)


def puo_modificare(attore: Any, utente: User) -> bool:
    """L'attore può intervenire su questo utente? (dati, password, permessi)"""
    if attore.is_superuser:
        return True
    if utente.is_superuser:
        return False
    return permessi_utente(utente) <= permessi_utente(attore)


def _registra(attore: Any, utente: User, azione: str, dettaglio: str = "") -> None:
    EventoUtente.objects.create(
        attore=attore if getattr(attore, "is_authenticated", False) else None,
        utente=utente,
        azione=azione,
        dettaglio=dettaglio[:2000],
    )


def _richiedi(attore: Any, codice: str, messaggio: str) -> None:
    if not ha_permesso(attore, codice):
        raise OperazioneUtenti(messaggio)


def _controlla_bersaglio(attore: Any, utente: User) -> None:
    if not puo_modificare(attore, utente):
        raise OperazioneUtenti(
            f"L'utente {utente.username} ha permessi che tu non hai: può modificarlo solo chi ha almeno i suoi "
            "permessi (o un superuser)."
        )


def _etichette(codici: Iterable[str]) -> str:
    nomi = {f.codice: f.etichetta for f in FUNZIONI}
    return ", ".join(sorted(nomi.get(c, c) for c in codici))


# --------------------------------------------------------------------------------------------- dati anagrafici
@transaction.atomic
def salva_dati(
    attore: Any,
    utente: User | None,
    *,
    username: str,
    nome: str,
    cognome: str,
    email: str,
    attivo: bool,
    cliente: bool,
    note: str = "",
    password: str = "",
    committenti: list[int] | None = None,
) -> User:
    """Crea o modifica i dati di un utente. Un utente nuovo nasce senza permessi.

    ``committenti``: clienti committenti che l'utente vede (None = non cambiarli). Un utente del Cliente ne deve
    avere almeno uno; con un solo committente attivo gli viene assegnato da solo."""
    _richiedi(attore, F.UTENTI_GESTIRE, "Non hai i permessi per creare o modificare gli utenti.")
    nuovo = utente is None
    if not nuovo:
        _controlla_bersaglio(attore, utente)
    username = username.strip()
    if not username:
        raise OperazioneUtenti("Il nome utente è obbligatorio.")
    if len(username) > 150 or not all(c.isalnum() or c in "@.+-_" for c in username):
        raise OperazioneUtenti("Nome utente non valido: lettere, cifre e @ . + - _ (massimo 150 caratteri).")
    if User.objects.filter(username__iexact=username).exclude(pk=getattr(utente, "pk", None)).exists():
        raise OperazioneUtenti(f"Esiste già un utente '{username}'.")
    email = email.strip()
    if email:
        from django.core.validators import validate_email

        try:
            validate_email(email)
        except ValidationError:
            raise OperazioneUtenti(f"Email non valida: {email}") from None
    if nuovo:
        if not password:
            raise OperazioneUtenti("Per un nuovo utente serve una password iniziale.")
        if not ha_permesso(attore, F.UTENTI_PASSWORD):
            raise OperazioneUtenti("Per impostare la password iniziale serve il permesso 'Reimpostare le password'.")
        utente = User(username=username)
        _valida_password(password, utente)
        utente.set_password(password)
    elif utente.pk == attore.pk and not attivo:
        raise OperazioneUtenti("Non puoi disattivare il tuo utente.")

    modifiche = []
    if not nuovo:
        for nome_campo, prima, dopo in (
            ("nome utente", utente.username, username),
            ("nome", utente.first_name, nome.strip()),
            ("cognome", utente.last_name, cognome.strip()),
            ("email", utente.email, email),
        ):
            if prima != dopo:
                modifiche.append(f"{nome_campo}: {prima or '—'} → {dopo or '—'}")
    era_attivo = utente.is_active
    utente.username, utente.first_name, utente.last_name = username, nome.strip()[:150], cognome.strip()[:150]
    utente.email, utente.is_active = email, attivo
    utente.save()

    p = profilo(utente)
    if p.note != note.strip():
        if not nuovo:
            modifiche.append("note")
        p.note = note.strip()
    if nuovo:
        p.deve_cambiare_password = True  # la password iniziale la conosce chi l'ha scritta
    p.save()

    from metodo.committenti.models import Committente

    attivi = Committente.objects.filter(attivo=True)
    if committenti is not None:
        scelti = list(attivi.filter(pk__in=committenti))
        if cliente and not scelti and attivi.count() == 1:
            scelti = list(attivi)
        if cliente and not scelti:
            raise OperazioneUtenti("Un utente del Cliente deve vedere almeno un cliente committente.")
        prima_c = set(p.committenti.values_list("pk", flat=True))
        if prima_c != {c.pk for c in scelti}:
            p.committenti.set(scelti)
            if not nuovo:
                modifiche.append("committenti: " + (", ".join(c.codice for c in scelti) or "tutti"))

    gruppo, _ = Group.objects.get_or_create(name=GRUPPO_CLIENTE)
    era_cliente = utente.groups.filter(pk=gruppo.pk).exists()
    if cliente != era_cliente:
        (utente.groups.add if cliente else utente.groups.remove)(gruppo)
        if not nuovo:
            modifiche.append("tipo: " + ("Cliente" if cliente else "Fornitore"))

    if nuovo:
        _registra(attore, utente, Azione.CREATO, "Cliente" if cliente else "Fornitore")
    else:
        if era_attivo != attivo:
            _registra(attore, utente, Azione.ATTIVATO if attivo else Azione.DISATTIVATO)
        if modifiche:
            _registra(attore, utente, Azione.DATI, "; ".join(modifiche))
    return utente


# --------------------------------------------------------------------------------------------- permessi
@transaction.atomic
def salva_permessi(attore: Any, utente: User, permessi: Iterable[str]) -> User:
    """Assegna i permessi scelti. Quelli che l'attore non ha restano come sono (non li può né dare né togliere)."""
    _richiedi(attore, F.UTENTI_PERMESSI, "Non hai i permessi per assegnare permessi.")
    _controlla_bersaglio(attore, utente)
    validi = tutti_i_codici()
    scelti = {p for p in permessi if p in validi}
    miei = concedibili(attore)
    attuali = permessi_utente(utente) if not utente.is_superuser else set()
    nuovi = (scelti & miei) | (attuali - miei)  # fuori dalla mia portata: invariati
    if utente.pk == attore.pk and not attore.is_superuser:
        mancanti = set(PERMESSI_AREA_UTENTI) & attuali - nuovi
        if mancanti:
            raise OperazioneUtenti("Non puoi toglierti i permessi dell'area Utenti e permessi.")
    if nuovi == attuali:
        return utente
    utente.user_permissions.set(permessi_di(nuovi))
    dettaglio = []
    if aggiunti := nuovi - attuali:
        dettaglio.append("aggiunti: " + _etichette(aggiunti))
    if tolti := attuali - nuovi:
        dettaglio.append("tolti: " + _etichette(tolti))
    _registra(attore, utente, Azione.PERMESSI, "; ".join(dettaglio))
    return utente


@transaction.atomic
def applica_profilo_utente(attore: Any, utente: User, nome: str) -> User:
    """Sostituisce i permessi con quelli del profilo (solo se l'attore li ha tutti)."""
    _richiedi(attore, F.UTENTI_PERMESSI, "Non hai i permessi per assegnare permessi.")
    _controlla_bersaglio(attore, utente)
    if nome not in PROFILI:
        raise OperazioneUtenti(f"Profilo sconosciuto: {nome}")
    codici = codici_profilo(nome)
    fuori = codici - concedibili(attore)
    if fuori:
        raise OperazioneUtenti(f"Il profilo {nome} contiene permessi che non hai: {_etichette(fuori)}.")
    toglierebbe = (set(PERMESSI_AREA_UTENTI) & permessi_utente(attore)) - codici
    if utente.pk == attore.pk and not attore.is_superuser and toglierebbe:
        raise OperazioneUtenti("Non puoi toglierti i permessi dell'area Utenti e permessi.")
    utente.user_permissions.set(permessi_di(codici))
    gruppo, _ = Group.objects.get_or_create(name=GRUPPO_CLIENTE)
    (utente.groups.add if nome == "Cliente" else utente.groups.remove)(gruppo)
    _registra(attore, utente, Azione.PROFILO, nome)
    return utente


# --------------------------------------------------------------------------------------------- password
def _valida_password(password: str, utente: User) -> None:
    try:
        validate_password(password, utente)
    except ValidationError as exc:
        raise OperazioneUtenti(" ".join(exc.messages)) from None


@transaction.atomic
def imposta_password(attore: Any, utente: User, password: str, *, da_cambiare: bool = True) -> None:
    _richiedi(attore, F.UTENTI_PASSWORD, "Non hai i permessi per reimpostare le password.")
    _controlla_bersaglio(attore, utente)
    if utente.pk == attore.pk:
        raise OperazioneUtenti("La tua password si cambia da 'Cambia password' nel menu utente.")
    _valida_password(password, utente)
    utente.set_password(password)
    utente.save(update_fields=["password"])
    p = profilo(utente)
    p.deve_cambiare_password = da_cambiare
    p.save(update_fields=["deve_cambiare_password"])
    _registra(attore, utente, Azione.PASSWORD, "da cambiare al prossimo accesso" if da_cambiare else "")


def _password_casuale() -> str:
    # Leggibile da dettare al telefono: niente caratteri ambigui (0/O, 1/l/I).
    alfabeto = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "-".join("".join(secrets.choice(alfabeto) for _ in range(4)) for _ in range(3))


@transaction.atomic
def genera_password_temporanea(attore: Any, utente: User) -> str:
    """Password casuale mostrata UNA volta a chi la genera; l'utente dovrà cambiarla al primo accesso."""
    _richiedi(attore, F.UTENTI_PASSWORD, "Non hai i permessi per reimpostare le password.")
    _controlla_bersaglio(attore, utente)
    if utente.pk == attore.pk:
        raise OperazioneUtenti("La tua password si cambia da 'Cambia password' nel menu utente.")
    password = _password_casuale()
    utente.set_password(password)
    utente.save(update_fields=["password"])
    p = profilo(utente)
    p.deve_cambiare_password = True
    p.save(update_fields=["deve_cambiare_password"])
    _registra(attore, utente, Azione.PASSWORD_TEMPORANEA)
    return password


@transaction.atomic
def cambia_propria_password(utente: User, attuale: str, nuova: str, conferma: str) -> None:
    if not utente.check_password(attuale):
        raise OperazioneUtenti("La password attuale non è corretta.")
    if nuova != conferma:
        raise OperazioneUtenti("Le due nuove password non coincidono.")
    if nuova == attuale:
        raise OperazioneUtenti("La nuova password deve essere diversa da quella attuale.")
    _valida_password(nuova, utente)
    utente.set_password(nuova)
    utente.save(update_fields=["password"])
    p = profilo(utente)
    p.deve_cambiare_password = False
    p.save(update_fields=["deve_cambiare_password"])
    _registra(utente, utente, Azione.PASSWORD_PROPRIA)


# --------------------------------------------------------------------------------------------- superuser
@transaction.atomic
def imposta_superuser(attore: Any, utente: User, valore: bool) -> None:
    if not attore.is_superuser:
        raise OperazioneUtenti("Solo un superuser può cambiare questa impostazione.")
    if utente.pk == attore.pk and not valore:
        raise OperazioneUtenti("Non puoi toglierti il ruolo di superuser da solo.")
    if utente.is_superuser == valore:
        return
    utente.is_superuser = valore
    utente.is_staff = valore  # l'admin tecnico di Django serve solo ai superuser
    utente.save(update_fields=["is_superuser", "is_staff"])
    _registra(attore, utente, Azione.SUPERUSER, "sì" if valore else "no")


__all__ = ["OperazioneUtenti", "is_cliente"]
