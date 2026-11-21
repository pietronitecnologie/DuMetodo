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

from dumetodo.core.models import Reparto
from dumetodo.core.permessi import F, ha_permesso
from dumetodo.ordini.servizi import OperazioneNonConsentita

from . import calcolo
from .models import Lavoratore, OrigineTimbratura, Postazione, Timbratura, TipoTimbratura

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
    _salva_codice(Lavoratore.objects.get(pk=lavoratore_id), codice)


def lavoratore_da_codice(codice: str) -> Lavoratore | None:
    codice = (codice or "").strip()
    if not codice.isdigit():
        return None
    return Lavoratore.objects.filter(codice_impronta=impronta(codice), attivo=True).select_related("reparto").first()


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


def salva_postazione(utente: Any, postazione_id: int | None, nome: str, reparto_id: Any, attiva: bool) -> Postazione:
    _richiedi(utente, F.TIMBRATURE_GESTIRE)
    p = Postazione.objects.get(pk=postazione_id) if postazione_id else Postazione(token=secrets.token_urlsafe(24))
    nome = (nome or "").strip()[:60]
    if not nome:
        raise OperazioneNonConsentita("Indica il nome della postazione.")
    if Postazione.objects.filter(nome__iexact=nome).exclude(pk=p.pk).exists():
        raise OperazioneNonConsentita(f"Esiste già una postazione «{nome}».")
    p.nome, p.attiva = nome, attiva
    p.reparto = Reparto.objects.filter(pk=reparto_id or None).first()
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


def azioni(postazione: Postazione, s: calcolo.Stato) -> list[str]:
    """Timbrature possibili adesso, in ordine (la prima è quella proposta)."""
    if s.in_mensa:
        return [T.FINE_MENSA]
    if s.in_pausa:
        return [T.FINE_PAUSA]
    if postazione.is_ingresso:
        return [T.USCITA, T.INIZIO_MENSA, T.INIZIO_PAUSA] if s.presente else [T.ENTRATA]
    # Postazione di reparto. Se lo stabilimento non ha una postazione di ingresso, si entra ed esce anche da qui.
    ingresso_qui = not esiste_ingresso()
    if not s.presente:
        return [T.INIZIO_REPARTO, T.ENTRATA] if ingresso_qui else []
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


def postazione_da_token(token: str) -> Postazione | None:
    p = Postazione.objects.filter(token=token, attiva=True).select_related("reparto").first()
    if p is not None and (p.ultimo_contatto is None or timezone.now() - p.ultimo_contatto > dt.timedelta(minutes=1)):
        Postazione.objects.filter(pk=p.pk).update(ultimo_contatto=timezone.now())
    return p


def identifica(postazione: Postazione, codice: str) -> dict[str, Any]:
    lav = _riconosci(postazione, codice)
    s = stato(lav)
    possibili = azioni(postazione, s)
    messaggio = "" if possibili else "Prima timbra l'entrata alla postazione di ingresso."
    return {
        "nome": f"{lav.nome} {lav.cognome}",
        "stato": descrivi_stato(s),
        "azioni": [{"tipo": t, "etichetta": _etichetta(t, postazione)} for t in possibili],
        "messaggio": messaggio,
    }


def _etichetta(tipo: str, postazione: Postazione) -> str:
    if tipo == T.INIZIO_REPARTO and postazione.reparto:
        return f"Inizio lavoro – {postazione.reparto.nome}"
    if tipo == T.FINE_REPARTO and postazione.reparto:
        return f"Fine lavoro – {postazione.reparto.nome}"
    return T(tipo).label


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
    if tipo == T.INIZIO_REPARTO and not attuale.presente:
        # Stabilimento senza postazione di ingresso: chi inizia in reparto entra anche in azienda.
        Timbratura.objects.create(
            lavoratore=lav, tipo=T.ENTRATA, momento=adesso, postazione=postazione, nota="entrata automatica"
        )
    return Timbratura.objects.create(
        lavoratore=lav,
        tipo=tipo,
        momento=adesso,
        postazione=postazione,
        reparto=postazione.reparto if tipo in (T.INIZIO_REPARTO, T.FINE_REPARTO) else None,
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
    from dumetodo.anagrafica.models import Articolo
    from dumetodo.configurazione import servizi as config
    from dumetodo.ordini.models import RigaOrdine, StatoRiga

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
