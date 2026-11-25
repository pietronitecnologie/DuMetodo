"""Timbrature: codici personali, postazioni, timbratura alla postazione, correzioni, presenze e costi per reparto."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from metodo.core.models import Reparto
from metodo.core.permessi import F, ha_permesso
from metodo.ordini.servizi import OperazioneNonConsentita

from . import calcolo
from .models import (
    Identificativo,
    Lavoratore,
    OrigineTimbratura,
    Postazione,
    Timbratura,
    TipoIdentificativo,
    TipoTimbratura,
)

T = TipoTimbratura
TENTATIVI_MASSIMI = 5  # codici sbagliati di fila prima del blocco della postazione
BLOCCO_SECONDI = 60
DOPPIA_SECONDI = 60  # stessa timbratura ripetuta entro un minuto: ignorata (doppio tocco)


def _richiedi(utente: Any, codice: str) -> None:
    if not ha_permesso(utente, codice):
        raise OperazioneNonConsentita("Non hai i permessi per questa operazione.")


# ---------------------------------------------------------------------------------------- codici personali
def impronta(codice: str) -> str:
    chiave = (settings.SECRET_KEY + ":timbrature").encode()
    return hmac.new(chiave, codice.strip().encode(), hashlib.sha256).hexdigest()


def _salva_codice(lavoratore: Lavoratore, codice: str) -> None:
    lavoratore.codice_impronta, lavoratore.codice_impostato_il = impronta(codice), timezone.now()
    try:
        with transaction.atomic():
            lavoratore.save(update_fields=["codice_impronta", "codice_impostato_il", "aggiornato_il"])
    except IntegrityError as exc:
        raise OperazioneNonConsentita("Questo codice è già di un altro lavoratore: scegline un altro.") from exc


def genera_codice(utente: Any, lavoratore_id: int) -> str:
    """Nuovo codice casuale di 6 cifre (mostrato una sola volta): il vecchio smette di funzionare."""
    _richiedi(utente, F.TIMBRATURE_GESTIRE)
    lav = Lavoratore.objects.get(pk=lavoratore_id)
    for _ in range(50):
        codice = f"{secrets.randbelow(900000) + 100000}"
        if not Lavoratore.objects.filter(codice_impronta=impronta(codice)).exists():
            _salva_codice(lav, codice)
            return codice
    raise OperazioneNonConsentita("Impossibile generare un codice libero: riprova.")  # pragma: no cover


def imposta_codice(utente: Any, lavoratore_id: int, codice: str) -> None:
    """Codice scelto (es. il numero del badge): da 4 a 10 cifre, diverso da quello degli altri."""
    _richiedi(utente, F.TIMBRATURE_GESTIRE)
    codice = (codice or "").strip()
    if not (codice.isdigit() and 4 <= len(codice) <= 10):
        raise OperazioneNonConsentita("Il codice deve avere da 4 a 10 cifre.")
    if Identificativo.objects.filter(valore=normalizza(codice)).exists():
        raise OperazioneNonConsentita("Questo numero è già un badge o una tessera: scegline un altro.")
    _salva_codice(Lavoratore.objects.get(pk=lavoratore_id), codice)


def normalizza(valore: str) -> str:
    """Valore letto da un lettore: senza spazi e separatori «:», in maiuscolo (gli UID NFC arrivano in modi diversi)."""
    return "".join(c for c in (valore or "") if not c.isspace() and c != ":").upper()[:64]


def lavoratore_da_codice(codice: str) -> Lavoratore | None:
    """Dal codice personale digitato o dal valore di un badge / tessera letto da un lettore o dalla fotocamera."""
    codice = (codice or "").strip()
    if not codice:
        return None
    if codice.isdigit():
        lav = Lavoratore.objects.filter(codice_impronta=impronta(codice), attivo=True).select_related("reparto").first()
        if lav is not None:
            return lav
    ident = (
        Identificativo.objects.filter(valore=normalizza(codice), attivo=True, lavoratore__attivo=True)
        .select_related("lavoratore__reparto")
        .first()
    )
    if ident is None:
        return None
    Identificativo.objects.filter(pk=ident.pk).update(ultimo_uso=timezone.now())
    return ident.lavoratore


_ALFABETO_BADGE = "ACDEFGHJKLMNPQRTUVWXY3479"  # senza caratteri che si confondono (0/O, 1/I, 5/S, 8/B…)


def aggiungi_identificativo(
    utente: Any, lavoratore_id: int, tipo: str, valore: str, descrizione: str = ""
) -> Identificativo:
    """Associa un badge o una tessera già esistenti (es. passando la tessera NFC sul lettore)."""
    _richiedi(utente, F.TIMBRATURE_GESTIRE)
    if tipo not in TipoIdentificativo.values:
        raise OperazioneNonConsentita("Tipo non valido.")
    v = normalizza(valore)
    if not 4 <= len(v) <= 64:
        raise OperazioneNonConsentita("Il codice letto deve avere da 4 a 64 caratteri: riprova la lettura.")
    altro = Identificativo.objects.filter(valore=v).select_related("lavoratore").first()
    if altro is not None:
        raise OperazioneNonConsentita(f"Questo {altro.get_tipo_display().lower()} è già di {altro.lavoratore}.")
    if v.isdigit() and Lavoratore.objects.filter(codice_impronta=impronta(v)).exists():
        raise OperazioneNonConsentita("Questo valore coincide con il codice personale di un lavoratore.")
    return Identificativo.objects.create(
        lavoratore=Lavoratore.objects.get(pk=lavoratore_id), tipo=tipo, valore=v, descrizione=(descrizione or "")[:60]
    )


def genera_badge(utente: Any, lavoratore_id: int) -> Identificativo:
    """Nuovo badge da stampare (codice a barre e QR con lo stesso valore, es. DM7KQ3XWA)."""
    _richiedi(utente, F.TIMBRATURE_GESTIRE)
    for _ in range(50):
        v = "DM" + "".join(secrets.choice(_ALFABETO_BADGE) for _ in range(8))
        if not Identificativo.objects.filter(valore=v).exists():
            return Identificativo.objects.create(
                lavoratore=Lavoratore.objects.get(pk=lavoratore_id),
                tipo=TipoIdentificativo.BADGE,
                valore=v,
                descrizione="badge stampato",
            )
    raise OperazioneNonConsentita("Impossibile generare un badge: riprova.")  # pragma: no cover


def disattiva_identificativo(utente: Any, identificativo_id: int, attivo: bool = False) -> Identificativo:
    """Badge perso o tessera restituita: non identifica più nessuno (riattivabile)."""
    _richiedi(utente, F.TIMBRATURE_GESTIRE)
    i = Identificativo.objects.get(pk=identificativo_id)
    i.attivo = attivo
    i.save(update_fields=["attivo"])
    return i


# ----------------------------------------------------------------------------------------------- anagrafiche
def salva_lavoratore(utente: Any, lavoratore_id: int | None, dati: dict[str, Any]) -> Lavoratore:
    _richiedi(utente, F.TIMBRATURE_GESTIRE)
    lav = Lavoratore.objects.get(pk=lavoratore_id) if lavoratore_id else Lavoratore()
    nome, cognome, matricola = (dati.get(k, "").strip() for k in ("nome", "cognome", "matricola"))
    if not (nome and cognome and matricola):
        raise OperazioneNonConsentita("Nome, cognome e matricola sono obbligatori.")
    if Lavoratore.objects.filter(matricola__iexact=matricola).exclude(pk=lav.pk).exists():
        raise OperazioneNonConsentita(f"La matricola {matricola} è già di un altro lavoratore.")
    lav.nome, lav.cognome, lav.matricola = nome[:60], cognome[:60], matricola[:30]
    lav.reparto = Reparto.objects.filter(pk=dati.get("reparto") or None).first()
    lav.note = (dati.get("note") or "").strip()[:200]
    lav.attivo = bool(dati.get("attivo", True))
    if "costo_orario" in dati and ha_permesso(utente, F.TIMBRATURE_COSTI):
        testo = str(dati.get("costo_orario") or "").strip().replace(",", ".")
        try:
            lav.costo_orario = Decimal(testo) if testo else None
        except ArithmeticError as exc:
            raise OperazioneNonConsentita("Costo orario non valido.") from exc
        if lav.costo_orario is not None and lav.costo_orario < 0:
            raise OperazioneNonConsentita("Il costo orario non può essere negativo.")
    lav.save()
    return lav


def salva_postazione(
    utente: Any,
    postazione_id: int | None,
    nome: str,
    reparto_id: Any,
    attiva: bool,
    metodi: dict[str, bool] | None = None,
) -> Postazione:
    _richiedi(utente, F.TIMBRATURE_GESTIRE)
    p = Postazione.objects.get(pk=postazione_id) if postazione_id else Postazione(token=secrets.token_urlsafe(24))
    nome = (nome or "").strip()[:60]
    if not nome:
        raise OperazioneNonConsentita("Indica il nome della postazione.")
    if Postazione.objects.filter(nome__iexact=nome).exclude(pk=p.pk).exists():
        raise OperazioneNonConsentita(f"Esiste già una postazione «{nome}».")
    p.nome, p.attiva = nome, attiva
    p.reparto = Reparto.objects.filter(pk=reparto_id or None).first()
    if metodi is not None:
        p.usa_tastierino = metodi.get("tastierino", False)
        p.usa_lettori = metodi.get("lettori", False)
        p.usa_fotocamera = metodi.get("fotocamera", False)
        if not (p.usa_tastierino or p.usa_lettori or p.usa_fotocamera):
            raise OperazioneNonConsentita("Attiva almeno un modo per identificarsi alla postazione.")
    p.save()
    return p


def rigenera_link(utente: Any, postazione_id: int) -> Postazione:
    """Nuovo link segreto: il vecchio smette di funzionare (es. PC dismesso o link finito nelle mani sbagliate)."""
    _richiedi(utente, F.TIMBRATURE_GESTIRE)
    p = Postazione.objects.get(pk=postazione_id)
    p.token = secrets.token_urlsafe(24)
    p.save(update_fields=["token", "aggiornato_il"])
    return p


# ------------------------------------------------------------------------------------------ stato e azioni
def _eventi(lavoratore_id: int, dal: dt.datetime, al: dt.datetime) -> list[calcolo.Evento]:
    return [
        calcolo.Evento(m, t, r)
        for m, t, r in Timbratura.objects.filter(
            lavoratore_id=lavoratore_id, annullata_il__isnull=True, momento__gte=dal, momento__lte=al
        ).values_list("momento", "tipo", "reparto_id")
    ]


def _giorno(m: dt.datetime) -> dt.date:
    return timezone.localtime(m).date()


def stato(lavoratore: Lavoratore, adesso: dt.datetime | None = None) -> calcolo.Stato:
    adesso = adesso or timezone.now()
    eventi = _eventi(lavoratore.pk, adesso - dt.timedelta(days=2), adesso)
    return calcolo.calcola(eventi, adesso, _giorno)[1]


def esiste_ingresso() -> bool:
    return Postazione.objects.filter(reparto__isnull=True, attiva=True).exists()


def _reparti_attivi() -> list[Reparto]:
    return list(Reparto.objects.filter(attivo=True).order_by("nome"))


def tipo_e_reparto(postazione: Postazione, tipo: str) -> tuple[str, int | None]:
    """«IRP:5» (scelta del reparto alla postazione di ingresso) -> ("IRP", 5); «IRP» -> reparto della postazione."""
    base, _, rid = tipo.partition(":")
    if rid:
        return base, int(rid) if rid.isdigit() else -1
    return base, postazione.reparto_id if base in (T.INIZIO_REPARTO, T.FINE_REPARTO) else None


def azioni(postazione: Postazione, s: calcolo.Stato) -> list[str]:
    """Timbrature possibili adesso, in ordine (la prima è quella proposta)."""
    if s.in_mensa:
        return [T.FINE_MENSA]
    if s.in_pausa:
        return [T.FINE_PAUSA]
    if postazione.is_ingresso:
        # Dall'ingresso si può anche andare (o passare) in qualsiasi reparto: «IRP:<id del reparto>».
        reparti = [r for r in _reparti_attivi() if r.pk != s.reparto_id]
        if not s.presente:
            return [T.ENTRATA, *(f"{T.INIZIO_REPARTO}:{r.pk}" for r in reparti)]
        lista = [T.USCITA, T.INIZIO_MENSA, T.INIZIO_PAUSA]
        if s.reparto_id:
            lista.append(f"{T.FINE_REPARTO}:{s.reparto_id}")
        return [*lista, *(f"{T.INIZIO_REPARTO}:{r.pk}" for r in reparti)]
    # Postazione di reparto. Se lo stabilimento non ha una postazione di ingresso, si entra ed esce anche da qui.
    ingresso_qui = not esiste_ingresso()
    if not s.presente:
        # Prima timbratura della giornata direttamente in reparto: si accetta e si registra anche l'entrata
        # (vedi ``timbra``), così il turno inizia comunque e non resta un buco nelle presenze.
        return [T.INIZIO_REPARTO, T.ENTRATA] if ingresso_qui else [T.INIZIO_REPARTO]
    if s.reparto_id == postazione.reparto_id:
        lista = [T.FINE_REPARTO, T.INIZIO_MENSA, T.INIZIO_PAUSA]
    else:
        lista = [T.INIZIO_REPARTO, T.INIZIO_MENSA, T.INIZIO_PAUSA]
    return [*lista, T.USCITA] if ingresso_qui else lista


def descrivi_stato(s: calcolo.Stato) -> str:
    if not s.presente:
        return "Non presente"
    if s.in_mensa:
        return "In mensa"
    if s.in_pausa:
        return "In pausa"
    if s.reparto_id:
        r = Reparto.objects.filter(pk=s.reparto_id).first()
        return f"Al lavoro in {r.nome}" if r else "Al lavoro"
    return "Presente (nessun reparto)"


# --------------------------------------------------------------------------------------------- alla postazione
def _chiave_blocco(postazione: Postazione) -> str:
    return f"timbrature:errori:{postazione.pk}"


def _controlla_blocco(postazione: Postazione) -> None:
    if (cache.get(_chiave_blocco(postazione)) or 0) >= TENTATIVI_MASSIMI:
        raise OperazioneNonConsentita("Troppi codici sbagliati: attendi un minuto e riprova.")


def _riconosci(postazione: Postazione, codice: str) -> Lavoratore:
    _controlla_blocco(postazione)
    lav = lavoratore_da_codice(codice)
    if lav is None:
        chiave = _chiave_blocco(postazione)
        cache.add(chiave, 0, BLOCCO_SECONDI)
        try:
            cache.incr(chiave)
        except ValueError:  # pragma: no cover - chiave scaduta nel frattempo
            cache.set(chiave, 1, BLOCCO_SECONDI)
        raise OperazioneNonConsentita("Codice non riconosciuto.")
    cache.delete(_chiave_blocco(postazione))
    return lav


COOKIE_POSTAZIONE = "dm_postazione"
SALE_COOKIE = "dumetodo.timbrature.postazione"  # invariato: i PC già associati restano associati
DURATA_COOKIE = 10 * 365 * 24 * 3600  # il PC resta associato finché non lo si scollega


def postazione_del_pc(request: Any) -> Postazione | None:
    """Postazione a cui è associato questo PC (cookie firmato con il token), se ancora valida."""
    token = request.get_signed_cookie(COOKIE_POSTAZIONE, default=None, salt=SALE_COOKIE)
    if not token:
        return None
    return Postazione.objects.filter(token=token, attiva=True).select_related("reparto").first()


def associa_pc(response: Any, request: Any, postazione: Postazione) -> None:
    response.set_signed_cookie(
        COOKIE_POSTAZIONE,
        postazione.token,
        salt=SALE_COOKIE,
        max_age=DURATA_COOKIE,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure(),
    )


def scollega_pc(response: Any) -> None:
    response.delete_cookie(COOKIE_POSTAZIONE, samesite="Lax")


def postazione_da_token(token: str) -> Postazione | None:
    p = Postazione.objects.filter(token=token, attiva=True).select_related("reparto").first()
    if p is not None and (p.ultimo_contatto is None or timezone.now() - p.ultimo_contatto > dt.timedelta(minutes=1)):
        Postazione.objects.filter(pk=p.pk).update(ultimo_contatto=timezone.now())
    return p


def identifica(postazione: Postazione, codice: str) -> dict[str, Any]:
    lav = _riconosci(postazione, codice)
    s = stato(lav)
    possibili = azioni(postazione, s)
    messaggio = "" if possibili else "Nessuna timbratura possibile adesso."
    return {
        "nome": f"{lav.nome} {lav.cognome}",
        "stato": descrivi_stato(s),
        "azioni": [{"tipo": t, "etichetta": _etichetta(t, postazione, s.presente)} for t in possibili],
        "messaggio": messaggio,
    }


def _etichetta(tipo: str, postazione: Postazione, presente: bool = True) -> str:
    base, reparto_id = tipo_e_reparto(postazione, tipo)
    reparto = (
        postazione.reparto if reparto_id == postazione.reparto_id else Reparto.objects.filter(pk=reparto_id).first()
    )
    if base == T.INIZIO_REPARTO and reparto:
        return f"{'Inizio lavoro' if presente else 'Entrata e inizio lavoro'} – {reparto.nome}"
    if base == T.FINE_REPARTO and reparto:
        return f"Fine lavoro – {reparto.nome}"
    return T(base).label


def etichetta_timbratura(t: Timbratura) -> str:
    if t.tipo == T.INIZIO_REPARTO and t.reparto:
        return f"Inizio lavoro – {t.reparto.nome}"
    if t.tipo == T.FINE_REPARTO and t.reparto:
        return f"Fine lavoro – {t.reparto.nome}"
    return T(t.tipo).label


@transaction.atomic
def timbra(postazione: Postazione, codice: str, tipo: str, adesso: dt.datetime | None = None) -> Timbratura:
    lav = _riconosci(postazione, codice)
    Lavoratore.objects.select_for_update().filter(pk=lav.pk).first()  # una timbratura per volta per lavoratore
    adesso = adesso or timezone.now()
    attuale = stato(lav, adesso)
    if tipo not in azioni(postazione, attuale):
        raise OperazioneNonConsentita("Questa timbratura non è possibile adesso: ripeti il codice.")
    if Timbratura.objects.filter(
        lavoratore=lav, tipo=tipo, annullata_il__isnull=True, momento__gte=adesso - dt.timedelta(seconds=DOPPIA_SECONDI)
    ).exists():
        raise OperazioneNonConsentita("Timbratura già registrata.")
    base, reparto_id = tipo_e_reparto(postazione, tipo)
    if base == T.INIZIO_REPARTO and not attuale.presente:
        # Chi inizia in un reparto senza aver timbrato l'entrata (stabilimento senza postazione di ingresso,
        # oppure reparto scelto all'ingresso) entra anche in azienda.
        Timbratura.objects.create(
            lavoratore=lav, tipo=T.ENTRATA, momento=adesso, postazione=postazione, nota="entrata automatica"
        )
    return Timbratura.objects.create(
        lavoratore=lav, tipo=base, momento=adesso, postazione=postazione, reparto_id=reparto_id
    )


# --------------------------------------------------------------------------------------------- correzioni
@transaction.atomic
def timbratura_manuale(
    utente: Any, lavoratore_id: int, tipo: str, momento: dt.datetime, reparto_id: int | None, nota: str
) -> Timbratura:
    """Timbratura dimenticata o sbagliata: si aggiunge a mano, con il motivo (resta nello storico)."""
    _richiedi(utente, F.TIMBRATURE_GESTIRE)
    if tipo not in T.values:
        raise OperazioneNonConsentita("Tipo di timbratura non valido.")
    if momento > timezone.now():
        raise OperazioneNonConsentita("Non si inseriscono timbrature nel futuro.")
    nota = (nota or "").strip()
    if not nota:
        raise OperazioneNonConsentita("Indica il motivo della correzione.")
    reparto = None
    if tipo in (T.INIZIO_REPARTO, T.FINE_REPARTO):
        reparto = Reparto.objects.filter(pk=reparto_id or None).first()
        if reparto is None:
            raise OperazioneNonConsentita("Indica il reparto.")
    return Timbratura.objects.create(
        lavoratore=Lavoratore.objects.get(pk=lavoratore_id),
        tipo=tipo,
        momento=momento,
        reparto=reparto,
        origine=OrigineTimbratura.MANUALE,
        nota=nota[:200],
        creata_da=utente,
    )


@transaction.atomic
def annulla(utente: Any, timbratura_id: int, motivo: str) -> Timbratura:
    """Le timbrature non si cancellano: si annullano con il motivo (e restano visibili)."""
    _richiedi(utente, F.TIMBRATURE_GESTIRE)
    t = Timbratura.objects.select_for_update().get(pk=timbratura_id)
    if t.annullata:
        raise OperazioneNonConsentita("La timbratura è già annullata.")
    motivo = (motivo or "").strip()
    if not motivo:
        raise OperazioneNonConsentita("Indica il motivo dell'annullamento.")
    t.annullata_il, t.annullata_da, t.motivo_annullamento = timezone.now(), utente, motivo[:200]
    t.save(update_fields=["annullata_il", "annullata_da", "motivo_annullamento"])
    return t


# -------------------------------------------------------------------------------------- presenze e costi
def _inizio_giorno(g: dt.date) -> dt.datetime:
    return timezone.make_aware(dt.datetime.combine(g, dt.time.min))


def turni(dal: dt.date, al: dt.date, lavoratori: Sequence[Lavoratore] | None = None) -> dict[int, list[calcolo.Turno]]:
    """Turni che iniziano tra ``dal`` e ``al`` (compresi), per lavoratore."""
    adesso = timezone.now()
    lavoratori = list(lavoratori) if lavoratori is not None else list(Lavoratore.objects.all())
    inizio, fine = _inizio_giorno(dal) - dt.timedelta(days=1), _inizio_giorno(al) + dt.timedelta(days=2)
    eventi: dict[int, list[calcolo.Evento]] = defaultdict(list)
    for lav_id, m, t, r in Timbratura.objects.filter(
        lavoratore__in=lavoratori, annullata_il__isnull=True, momento__gte=inizio, momento__lte=fine
    ).values_list("lavoratore_id", "momento", "tipo", "reparto_id"):
        eventi[lav_id].append(calcolo.Evento(m, t, r))
    risultato = {}
    for lav in lavoratori:
        tt, _ = calcolo.calcola(eventi.get(lav.pk, []), adesso, _giorno)
        risultato[lav.pk] = [x for x in tt if dal <= x.giorno <= al]
    return risultato


def ore(secondi: float) -> Decimal:
    return (Decimal(str(secondi)) / 3600).quantize(Decimal("0.01"))


@dataclass
class RigaPresenze:
    lavoratore: Lavoratore
    turni: list[calcolo.Turno]
    stato: str = ""
    per_reparto: dict[int, float] = field(default_factory=dict)

    @property
    def entrata(self) -> dt.datetime | None:
        return self.turni[0].inizio if self.turni else None

    @property
    def uscita(self) -> dt.datetime | None:
        return self.turni[-1].fine if self.turni and not self.turni[-1].in_corso else None

    @property
    def lavorato(self) -> Decimal:
        return ore(sum(t.lavorato for t in self.turni))

    @property
    def mensa_pausa(self) -> Decimal:
        return ore(sum(t.mensa + t.pausa for t in self.turni))

    @property
    def non_assegnato(self) -> Decimal:
        return ore(sum(t.non_assegnato for t in self.turni))

    @property
    def anomalie(self) -> list[str]:
        return [a for t in self.turni for a in t.anomalie]

    @property
    def in_corso(self) -> bool:
        return any(t.in_corso for t in self.turni)


def presenze(dal: dt.date, al: dt.date, solo_attivi: bool = True) -> tuple[list[RigaPresenze], list[Reparto]]:
    qs = Lavoratore.objects.select_related("reparto")
    if solo_attivi:
        qs = qs.filter(attivo=True)
    lavoratori = list(qs)
    per_lav = turni(dal, al, lavoratori)
    oggi = timezone.localdate()
    righe, usati = [], set()
    for lav in lavoratori:
        tt = per_lav.get(lav.pk, [])
        r = RigaPresenze(lav, tt)
        tot: dict[int, float] = defaultdict(float)
        for t in tt:
            for rep, sec in t.per_reparto.items():
                tot[rep] += sec
        r.per_reparto = {k: ore(v) for k, v in tot.items()}
        usati |= set(tot)
        if dal <= oggi <= al:
            r.stato = descrivi_stato(stato(lav))
        righe.append(r)
    reparti = list(Reparto.objects.filter(pk__in=usati).order_by("nome"))
    return righe, reparti


def costi_per_reparto(dal: dt.date, al: dt.date) -> dict[str, Any]:
    """Ore lavorate e costo per reparto nel periodo, confrontati con le ore teoriche consegnate dal reparto."""
    from metodo.anagrafica.models import Articolo
    from metodo.configurazione import servizi as config
    from metodo.ordini.models import RigaOrdine, StatoRiga

    lavoratori = list(Lavoratore.objects.all())
    costo = {lav.pk: lav.costo_orario for lav in lavoratori}
    sec: dict[int | None, float] = defaultdict(float)
    euro: dict[int | None, Decimal] = defaultdict(Decimal)
    senza_costo: set[int] = set()
    for lav_id, tt in turni(dal, al, lavoratori).items():
        for t in tt:
            for rep, s in [*t.per_reparto.items(), (None, t.non_assegnato)]:
                if not s:
                    continue
                sec[rep] += s
                if costo[lav_id] is None:
                    senza_costo.add(lav_id)
                else:
                    euro[rep] += Decimal(str(s)) / 3600 * costo[lav_id]
    # Ore teoriche consegnate: valore delle righe in bolla nel periodo / resa oraria, per reparto dell'articolo.
    resa = config.leggi("produzione.prezzo_resa_oraria")
    reparto_di = dict(Articolo.objects.exclude(reparto=None).values_list("codice", "reparto_id"))
    teoriche: dict[int, Decimal] = defaultdict(Decimal)
    if resa:
        for art, qta, prezzo in (
            RigaOrdine.objects.filter(stato=StatoRiga.ATTIVA, bolla_emessa=True, data_consegna__range=(dal, al))
            .exclude(prezzo_unitario=None)
            .values_list("articolo", "qta", "prezzo_unitario")
        ):
            if art in reparto_di:
                teoriche[reparto_di[art]] += qta * prezzo / resa
    reparti = {r.pk: r for r in Reparto.objects.filter(pk__in=[k for k in sec if k] + list(teoriche))}
    righe = []
    for rep_id in sorted(set(sec) | set(teoriche), key=lambda k: (k is None, reparti[k].nome if k else "")):
        h = ore(sec.get(rep_id, 0))
        t = teoriche.get(rep_id) if rep_id else None
        c = euro.get(rep_id, Decimal(0)).quantize(Decimal("0.01"))
        righe.append(
            {
                "reparto": reparti[rep_id].nome if rep_id else "Non assegnato a un reparto",
                "ore": h,
                "costo": c,
                "teoriche": t.quantize(Decimal("0.01")) if t is not None else None,
                "efficienza": (t / h * 100).quantize(Decimal("1")) if t and h else None,
                "costo_ora_teorica": (c / t).quantize(Decimal("0.01")) if t and c else None,
            }
        )
    return {
        "righe": righe,
        "totale_ore": sum((r["ore"] for r in righe), Decimal(0)),
        "totale_costo": sum((r["costo"] for r in righe), Decimal(0)),
        "totale_teoriche": sum((r["teoriche"] or Decimal(0) for r in righe), Decimal(0)),
        "senza_costo": Lavoratore.objects.filter(pk__in=senza_costo).order_by("cognome"),
        "resa_configurata": bool(resa),
    }
