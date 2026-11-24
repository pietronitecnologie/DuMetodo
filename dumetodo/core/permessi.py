"""Permessi granulari: ogni funzione del sito è un permesso assegnabile al singolo utente.

- Le FUNZIONI sono dichiarate qui, raggruppate per sezione (Produzione, Report, Utilità,
  Amministrazione). Ognuna diventa un ``Permission`` Django (``core.<codice>``), creato/aggiornato
  automaticamente a ogni ``migrate``.
- I PROFILI (Operatore, Caporeparto, Amministratore, Cliente) sono solo modelli: applicare un
  profilo a un utente gli assegna quell'insieme di permessi, che poi si può personalizzare
  funzione per funzione dalla pagina "Utenti e permessi".
- Il gruppo "Cliente" resta un'IDENTITÀ (non un permesso): dice se l'utente scrive come Cliente
  o come Fornitore nelle note, e nasconde le note interne.
- Superuser: ha tutti i permessi, TRANNE le funzioni RISERVATE (sezione Gestionale: DDT di legge e
  fatture): quelle valgono solo se assegnate esplicitamente all'utente (profilo "Amministrazione"), e
  non arrivano agli amministratori completi né con il profilo "Amministratore".

Nel codice: ``@richiede_permesso(F.ORDINI_VEDERE)`` sulle viste, ``ha_permesso(utente, F.X)``
nei servizi, ``{% if perms.core.ordini_prezzi %}`` nei template.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import wraps
from typing import Any

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse

APP = "core"
GRUPPO_CLIENTE = "Cliente"


class Sezione(StrEnum):
    PRODUZIONE = "Produzione"
    MAGAZZINO = "Magazzino"
    QUALITA = "Archivio tracce"
    REPORT = "Report"
    UTILITA = "Utilità"
    AMMINISTRAZIONE = "Amministrazione"
    UTENTI = "Utenti e permessi"
    GESTIONALE = "Gestionale (riservato)"
    TIMBRATURE = "Timbrature"
    TRACCIATURA = "Tracciatura"


@dataclass(frozen=True)
class Funzione:
    codice: str
    sezione: Sezione
    etichetta: str
    descrizione: str = ""
    # Riservata: mai inclusa in "tutte le funzioni" (profilo Amministratore, superuser, nuove funzioni).
    riservata: bool = False


class F:
    """Codici delle funzioni (costanti, per evitare errori di battitura)."""

    ORDINI_VEDERE = "ordini_vedere"
    ORDINI_PREZZI = "ordini_prezzi"
    ORDINI_STATO = "ordini_stato"
    ORDINI_RIGHE = "ordini_righe"
    ORDINI_DIVIDI = "ordini_dividi"
    ORDINI_ANNULLA = "ordini_annulla"
    NOTE_SCRIVERE = "note_scrivere"
    NOTE_ELIMINARE_TUTTE = "note_eliminare_tutte"
    IMPORTAZIONE = "importazione"
    IMPORTAZIONE_AVANZATA = "importazione_avanzata"
    CONSEGNA = "consegna"
    DDT = "ddt"
    DDT_REINVIA = "ddt_reinvia"
    AGENDA = "agenda"
    PIANIFICAZIONE = "pianificazione"
    PIANIFICAZIONE_CAPACITA = "pianificazione_capacita"
    PIANIFICAZIONE_REPORT_LIBERO = "pianificazione_report_libero"
    MAGAZZINO_ANAGRAFICHE = "magazzino_anagrafiche"
    MAGAZZINO_INVENTARIO = "magazzino_inventario"
    MAGAZZINO_RICHIESTE = "magazzino_richieste"
    MAGAZZINO_RICHIESTE_GESTIONE = "magazzino_richieste_gestione"
    MAGAZZINO_DISTINTE = "magazzino_distinte"
    MAGAZZINO_DISTINTE_MODIFICA = "magazzino_distinte_modifica"
    MAGAZZINO_DDT = "magazzino_ddt"
    MAGAZZINO_DDT_ELIMINA = "magazzino_ddt_elimina"
    TRACCIABILITA_CONSULTA = "tracciabilita_consulta"
    REPORT_DASHBOARD = "report_dashboard"
    REPORT_PRODUTTIVITA = "report_produttivita"
    REPORT_MATERIALE = "report_materiale"
    REPORT_PREZZI = "report_prezzi"
    UTILITA_ERRORI_IMPORTAZIONE = "utilita_errori_importazione"
    UTILITA_RISTAMPA_ETICHETTE = "utilita_ristampa_etichette"
    UTILITA_ANNUNCI = "utilita_annunci"
    UTILITA_SCORPORAZIONE = "utilita_scorporazione"
    UTILITA_CAMBIA_ORDINE = "utilita_cambia_ordine"
    CONFIGURAZIONE = "configurazione"
    UTENTI_VEDERE = "utenti_vedere"
    UTENTI_GESTIRE = "utenti_gestire"
    UTENTI_PASSWORD = "utenti_password"  # noqa: S105 - nome di un permesso, non una password
    UTENTI_PERMESSI = "utenti_permessi"
    REPARTI = "reparti"
    CHAT = "chat"
    TRACCIATURA_VEDERE = "tracciatura_vedere"
    TRACCIATURA_REGISTRA = "tracciatura_registra"
    TRACCIATURA_CONFIGURA = "tracciatura_configura"
    TIMBRATURE_VEDERE = "timbrature_vedere"
    TIMBRATURE_GESTIRE = "timbrature_gestire"
    TIMBRATURE_COSTI = "timbrature_costi"
    GESTIONALE_VEDERE = "gestionale_vedere"
    GESTIONALE_DDT = "gestionale_ddt"
    GESTIONALE_FATTURE = "gestionale_fatture"
    GESTIONALE_IMPOSTAZIONI = "gestionale_impostazioni"


FUNZIONI: list[Funzione] = [
    Funzione(F.ORDINI_VEDERE, Sezione.PRODUZIONE, "Vedere gli ordini", "Elenco e dettaglio ordini, storico scadenze."),
    Funzione(F.ORDINI_PREZZI, Sezione.PRODUZIONE, "Vedere i prezzi delle righe"),
    Funzione(
        F.ORDINI_STATO, Sezione.PRODUZIONE, "Cambiare lo stato degli ordini", "Lavorato, Spedisci, Rimuovi da consegna."
    ),
    Funzione(F.ORDINI_RIGHE, Sezione.PRODUZIONE, "Gestire le righe", "Evadi, Blocca, Scorpora/Reincorpora."),
    Funzione(F.ORDINI_DIVIDI, Sezione.PRODUZIONE, "Dividere righe e posticipare scadenze"),
    Funzione(
        F.ORDINI_ANNULLA,
        Sezione.PRODUZIONE,
        "Annullare, consegnare forzatamente e riaprire ordini",
        "Comprende annullare righe e modificare righe già in spedizione.",
    ),
    Funzione(F.NOTE_SCRIVERE, Sezione.PRODUZIONE, "Scrivere note sugli ordini"),
    Funzione(F.NOTE_ELIMINARE_TUTTE, Sezione.PRODUZIONE, "Eliminare le note di altri utenti"),
    Funzione(F.IMPORTAZIONE, Sezione.PRODUZIONE, "Importare ordini", "Caricamento file e consultazione importazioni."),
    Funzione(F.IMPORTAZIONE_AVANZATA, Sezione.PRODUZIONE, "Rielaborare file e leggere subito la casella"),
    Funzione(F.CONSEGNA, Sezione.PRODUZIONE, "Consegnare (bolle ed etichette)"),
    Funzione(F.DDT, Sezione.PRODUZIONE, "Registrare DDT e inviare il file tecnico"),
    Funzione(F.DDT_REINVIA, Sezione.PRODUZIONE, "Reinviare il file tecnico di un DDT"),
    Funzione(F.AGENDA, Sezione.PRODUZIONE, "Vedere le agende ordini e pulsanti"),
    Funzione(F.PIANIFICAZIONE, Sezione.PRODUZIONE, "Pianificazione e tabulati"),
    Funzione(F.PIANIFICAZIONE_CAPACITA, Sezione.PRODUZIONE, "Modificare la capacità dei reparti"),
    Funzione(
        F.PIANIFICAZIONE_REPORT_LIBERO, Sezione.PRODUZIONE, "Inviare il report di pianificazione a indirizzi liberi"
    ),
    Funzione(F.MAGAZZINO_ANAGRAFICHE, Sezione.MAGAZZINO, "Anagrafiche componenti e articoli finiti"),
    Funzione(F.MAGAZZINO_INVENTARIO, Sezione.MAGAZZINO, "Inventario"),
    Funzione(F.MAGAZZINO_RICHIESTE, Sezione.MAGAZZINO, "Richiedere materiali"),
    Funzione(
        F.MAGAZZINO_RICHIESTE_GESTIONE,
        Sezione.MAGAZZINO,
        "Gestire le richieste materiali",
        "Inviare le email, segnare ricevuto/urgente, eliminare.",
    ),
    Funzione(F.MAGAZZINO_DISTINTE, Sezione.MAGAZZINO, "Vedere le distinte base"),
    Funzione(F.MAGAZZINO_DISTINTE_MODIFICA, Sezione.MAGAZZINO, "Modificare le distinte base"),
    Funzione(F.MAGAZZINO_DDT, Sezione.MAGAZZINO, "DDT in ingresso", "Inserimento, importazione, verifica, ricerca."),
    Funzione(F.MAGAZZINO_DDT_ELIMINA, Sezione.MAGAZZINO, "Eliminare righe dei DDT in ingresso"),
    Funzione(
        F.TRACCIABILITA_CONSULTA,
        Sezione.QUALITA,
        "Consultare l'archivio delle tracce",
        "Tracce registrate in passato (anche importate da Metodo), in sola lettura.",
    ),
    Funzione(F.REPORT_DASHBOARD, Sezione.REPORT, "Dashboard"),
    Funzione(F.REPORT_PRODUTTIVITA, Sezione.REPORT, "Analisi produttività"),
    Funzione(F.REPORT_MATERIALE, Sezione.REPORT, "Materiale per reparto e settimana"),
    Funzione(F.REPORT_PREZZI, Sezione.REPORT, "Controllo prezzi (bolle e ordini in lavorazione)"),
    Funzione(
        F.UTILITA_ERRORI_IMPORTAZIONE,
        Sezione.UTILITA,
        "Errori di importazione",
        "Consultare e segnare come gestiti i problemi di importazione.",
    ),
    Funzione(F.UTILITA_RISTAMPA_ETICHETTE, Sezione.UTILITA, "Ristampa etichette da DDT"),
    Funzione(F.UTILITA_ANNUNCI, Sezione.UTILITA, "Gestione annunci"),
    Funzione(F.UTILITA_SCORPORAZIONE, Sezione.UTILITA, "Scorporazione di massa"),
    Funzione(F.CHAT, Sezione.UTILITA, "Usare la chat", "Chat 1 a 1 con gli altri utenti nel client desktop."),
    Funzione(F.UTILITA_CAMBIA_ORDINE, Sezione.UTILITA, "Cambia ordine (numero, serie, appendice, scadenza)"),
    Funzione(F.CONFIGURAZIONE, Sezione.AMMINISTRAZIONE, "Configurazione"),
    Funzione(
        F.TRACCIATURA_VEDERE,
        Sezione.TRACCIATURA,
        "Vedere le tracciature",
        "Elenco, dettaglio, foto e documenti; riquadro nelle pagine di ordini e DDT.",
    ),
    Funzione(
        F.TRACCIATURA_REGISTRA,
        Sezione.TRACCIATURA,
        "Registrare tracciature",
        "Compilare i modelli: controlli, lotti, operazioni, documenti e foto; annullare le proprie.",
    ),
    Funzione(
        F.TRACCIATURA_CONFIGURA,
        Sezione.TRACCIATURA,
        "Configurare i modelli di tracciatura",
        "Campi, limiti, collegamenti e codici a cui si applicano; annullare le tracciature di tutti.",
    ),
    Funzione(
        F.TIMBRATURE_VEDERE,
        Sezione.TIMBRATURE,
        "Vedere presenze e timbrature",
        "Chi è presente e dove, ore per lavoratore e per reparto, elenco delle timbrature.",
    ),
    Funzione(
        F.TIMBRATURE_GESTIRE,
        Sezione.TIMBRATURE,
        "Gestire lavoratori, postazioni e correzioni",
        "Anagrafica lavoratori e codici personali, postazioni (link per il client), timbrature manuali e annullamenti.",
    ),
    Funzione(
        F.TIMBRATURE_COSTI,
        Sezione.TIMBRATURE,
        "Costi orari e costi per reparto",
        "Vedere e modificare il costo orario dei lavoratori; report dei costi per reparto.",
    ),
    Funzione(
        F.GESTIONALE_VEDERE,
        Sezione.GESTIONALE,
        "Vedere bolle e fatture",
        "Elenchi, stampa dei DDT e delle copie di cortesia, download degli XML.",
        riservata=True,
    ),
    Funzione(
        F.GESTIONALE_DDT,
        Sezione.GESTIONALE,
        "Emettere e annullare i DDT",
        "Bolle di trasporto con numerazione di legge, dalle righe consegnate.",
        riservata=True,
    ),
    Funzione(
        F.GESTIONALE_FATTURE,
        Sezione.GESTIONALE,
        "Emettere fatture e note di credito",
        "Fatture differite dai DDT e note di credito in formato FatturaPA (XML).",
        riservata=True,
    ),
    Funzione(
        F.GESTIONALE_IMPOSTAZIONI,
        Sezione.GESTIONALE,
        "Dati fiscali e impostazioni",
        "Dati fiscali di Fornitore e Cliente, IVA, pagamento, valori predefiniti dei DDT.",
        riservata=True,
    ),
    Funzione(F.REPARTI, Sezione.AMMINISTRAZIONE, "Gestire i reparti", "Codice, nome, capacità settimanale, gruppo."),
    Funzione(F.UTENTI_VEDERE, Sezione.UTENTI, "Vedere utenti e permessi", "Elenco utenti, permessi e storico."),
    Funzione(
        F.UTENTI_GESTIRE,
        Sezione.UTENTI,
        "Creare e modificare utenti",
        "Nome utente, nome, email, tipo (Fornitore/Cliente), attivazione e disattivazione.",
    ),
    Funzione(
        F.UTENTI_PASSWORD,
        Sezione.UTENTI,
        "Reimpostare le password",
        "Imposta o genera una password temporanea (da cambiare al primo accesso).",
    ),
    Funzione(
        F.UTENTI_PERMESSI,
        Sezione.UTENTI,
        "Assegnare permessi e profili",
        "Si possono assegnare solo i permessi che si hanno già.",
    ),
]

_OPERATORE = {
    F.ORDINI_VEDERE,
    F.ORDINI_STATO,
    F.ORDINI_RIGHE,
    F.NOTE_SCRIVERE,
    F.IMPORTAZIONE,
    F.CONSEGNA,
    F.DDT,
    F.AGENDA,
    F.PIANIFICAZIONE,
    F.UTILITA_ERRORI_IMPORTAZIONE,
    F.UTILITA_RISTAMPA_ETICHETTE,
    F.UTILITA_ANNUNCI,
    F.MAGAZZINO_ANAGRAFICHE,
    F.MAGAZZINO_INVENTARIO,
    F.MAGAZZINO_RICHIESTE,
    F.MAGAZZINO_DISTINTE,
    F.MAGAZZINO_DDT,
    F.TRACCIABILITA_CONSULTA,
    F.CHAT,
    F.TRACCIATURA_VEDERE,
    F.TRACCIATURA_REGISTRA,
}
_CAPOREPARTO = _OPERATORE | {F.ORDINI_DIVIDI, F.PIANIFICAZIONE_CAPACITA, F.UTENTI_VEDERE, F.TIMBRATURE_VEDERE}
PROFILI: dict[str, set[str]] = {
    "Operatore": _OPERATORE,
    "Caporeparto": _CAPOREPARTO,
    "Amministratore": {"*"},  # tutte le funzioni non riservate
    # Ufficio amministrativo: l'unico profilo con la sezione Gestionale (funzioni riservate).
    "Amministrazione": {
        F.ORDINI_VEDERE,
        F.ORDINI_PREZZI,
        F.NOTE_SCRIVERE,
        F.CONSEGNA,
        F.DDT,
        F.GESTIONALE_VEDERE,
        F.GESTIONALE_DDT,
        F.GESTIONALE_FATTURE,
        F.GESTIONALE_IMPOSTAZIONI,
        F.TIMBRATURE_VEDERE,
        F.TIMBRATURE_COSTI,
    },
    "Cliente": {F.ORDINI_VEDERE, F.NOTE_SCRIVERE, F.AGENDA, F.TRACCIABILITA_CONSULTA},
}


# Gruppo in Metodo -> profilo in DuMetodo (migrazione utenti).
GRUPPI_METODO: dict[str, str] = {
    "Geko": "Operatore",
    "GekoCapRep": "Caporeparto",
    "Geko Admin": "Amministratore",
    "Customer": "Cliente",
}
# Gruppi-ruolo delle prime versioni di DuMetodo, convertiti in permessi individuali a ogni migrate.
GRUPPI_RUOLO_OBSOLETI = ("Operatore", "Caporeparto", "Amministratore")


def registra_funzioni(funzioni: Iterable[Funzione], profili: dict[str, Iterable[str]] | None = None) -> None:
    """Permette ad altre app di aggiungere le proprie funzioni (chiamata a import time dal loro ``permessi.py``)."""
    codici = {f.codice for f in FUNZIONI}
    for f in funzioni:
        if f.codice in codici:
            raise ValueError(f"Funzione duplicata: {f.codice}")
        FUNZIONI.append(f)
        codici.add(f.codice)
    for nome, extra in (profili or {}).items():
        PROFILI[nome] |= set(extra)


def riservate() -> set[str]:
    return {f.codice for f in FUNZIONI if f.riservata}


def codici_profilo(nome: str) -> set[str]:
    codici = PROFILI[nome]
    return {f.codice for f in FUNZIONI if not f.riservata} if "*" in codici else set(codici)


# --------------------------------------------------------------------------------------------- controlli
def ha_permesso(user: Any, *codici: str) -> bool:
    """True se l'utente (attivo) ha ALMENO UNO dei permessi indicati."""
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    riservati = riservate()
    for c in codici:
        if c in riservati:
            if c in _assegnati(user):
                return True
        elif user.has_perm(f"{APP}.{c}"):
            return True
    return False


def _assegnati(user: Any) -> set[str]:
    """Permessi di DuMetodo assegnati esplicitamente all'utente (anche via gruppi): per le funzioni riservate
    non vale l'"ha tutto" del superuser. In cache sull'oggetto utente, come fa Django per has_perm."""
    cache = getattr(user, "_dm_assegnati", None)
    if cache is None:
        from django.contrib.auth.models import Permission
        from django.db.models import Q

        cache = set(
            Permission.objects.filter(content_type__app_label=APP, content_type__model="funzionalita")
            .filter(Q(user=user) | Q(group__user=user))
            .values_list("codename", flat=True)
        )
        user._dm_assegnati = cache
    return cache


def is_cliente(user: Any) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    cache = getattr(user, "_is_cliente_cache", None)
    if cache is None:
        cache = user.groups.filter(name=GRUPPO_CLIENTE).exists()
        user._is_cliente_cache = cache
    return cache


def is_fornitore(user: Any) -> bool:
    """Utente del Fornitore (scrive note come Fornitore e vede le note interne)."""
    return getattr(user, "is_authenticated", False) and user.is_active and not is_cliente(user)


def richiede_permesso(*codici: str) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    """Decoratore: 302 al login se anonimo, 403 se manca il permesso (basta uno dei codici)."""

    def decorator(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @wraps(view)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login

                return redirect_to_login(request.get_full_path())
            if not ha_permesso(request.user, *codici):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


class PermessoRichiestoMixin(AccessMixin):
    permessi_richiesti: tuple[str, ...] = ()

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not ha_permesso(request.user, *self.permessi_richiesti):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]


# --------------------------------------------------------------------------------------------- sincronizzazione
def sincronizza_permessi(**kwargs: Any) -> None:
    """post_migrate: crea/aggiorna i Permission delle funzioni ed elimina quelli non più dichiarati."""
    from django.contrib.auth.models import Permission
    from django.contrib.contenttypes.models import ContentType

    from .models import Funzionalita

    ct = ContentType.objects.get_for_model(Funzionalita, for_concrete_model=False)
    dichiarati = {f.codice: f.etichetta[:255] for f in FUNZIONI}
    # Funzioni divise in più funzioni: chi aveva la vecchia riceve tutte le nuove (prima che venga eliminata).
    titolari_rinominati = {
        vecchio: list(esistenti_utenti(ct, vecchio)) for vecchio in RINOMINATI if vecchio not in dichiarati
    }
    esistenti = {p.codename: p for p in Permission.objects.filter(content_type=ct)}
    # Chi ha TUTTE le funzioni esistenti (amministratore completo) riceverà anche quelle nuove:
    # altrimenti ogni aggiornamento che aggiunge una funzione la renderebbe inaccessibile a tutti.
    riservati = riservate()
    normali = [p for c, p in esistenti.items() if c not in riservati]
    amministratori = _utenti_con_tutti(normali) if normali else []
    nuovi = []
    for codice, nome in dichiarati.items():
        p = esistenti.get(codice)
        if p is None:
            nuovi.append(Permission.objects.create(content_type=ct, codename=codice, name=nome))
        elif p.name != nome:
            p.name = nome
            p.save(update_fields=["name"])
    for vecchio, utenti in titolari_rinominati.items():
        nuovi_perm = list(Permission.objects.filter(content_type=ct, codename__in=RINOMINATI[vecchio]))
        for u in utenti:
            u.user_permissions.add(*nuovi_perm)
    Permission.objects.filter(content_type=ct).exclude(codename__in=dichiarati).delete()
    nuovi = [p for p in nuovi if p.codename not in riservati]
    if nuovi:
        for u in amministratori:
            u.user_permissions.add(*nuovi)
    _converti_gruppi_ruolo()


# Vecchio codice -> codici che lo sostituiscono.
RINOMINATI: dict[str, tuple[str, ...]] = {
    "utenti": (F.UTENTI_VEDERE, F.UTENTI_GESTIRE, F.UTENTI_PASSWORD, F.UTENTI_PERMESSI),
    # Tracce, controlli qualità e foto ora si registrano con il modulo Tracciatura.
    "tracciabilita_registra": (F.TRACCIABILITA_CONSULTA, F.TRACCIATURA_VEDERE, F.TRACCIATURA_REGISTRA),
    "qualita_test": (F.TRACCIATURA_CONFIGURA,),
}


def esistenti_utenti(ct: Any, codice: str) -> Any:
    from django.contrib.auth.models import User

    return User.objects.filter(user_permissions__content_type=ct, user_permissions__codename=codice).distinct()


def _utenti_con_tutti(permessi: Iterable[Any]) -> list[Any]:
    from django.contrib.auth.models import User
    from django.db.models import Count, Q

    ids = [p.pk for p in permessi]
    return list(
        User.objects.annotate(n=Count("user_permissions", filter=Q(user_permissions__in=ids))).filter(n=len(ids))
    )


def _converti_gruppi_ruolo() -> None:
    """Gli utenti nei vecchi gruppi-ruolo ricevono i permessi del profilo (in aggiunta) e il gruppo sparisce."""
    from django.contrib.auth.models import Group

    for nome in GRUPPI_RUOLO_OBSOLETI:
        gruppo = Group.objects.filter(name=nome).first()
        if gruppo is None:
            continue
        perms = list(permessi_di(codici_profilo(nome)))
        for u in gruppo.user_set.all():
            u.user_permissions.add(*perms)
        gruppo.delete()


def permessi_di(codici: Iterable[str]) -> Any:
    from django.contrib.auth.models import Permission

    return Permission.objects.filter(
        content_type__app_label=APP, content_type__model="funzionalita", codename__in=list(codici)
    )


def applica_profilo(user: Any, nome: str, *altri: str) -> None:
    """Sostituisce i permessi individuali dell'utente con quelli del profilo (o dell'unione dei profili)
    e imposta l'identità Cliente solo se TUTTI i profili sono "Cliente"."""
    from django.contrib.auth.models import Group

    nomi = (nome, *altri)
    codici: set[str] = set().union(*(codici_profilo(n) for n in nomi))
    user.user_permissions.set(permessi_di(codici))
    cliente, _ = Group.objects.get_or_create(name=GRUPPO_CLIENTE)
    if all(n == "Cliente" for n in nomi):
        user.groups.add(cliente)
    else:
        user.groups.remove(cliente)
    for attr in ("_perm_cache", "_user_perm_cache", "_is_cliente_cache"):
        if hasattr(user, attr):
            delattr(user, attr)
