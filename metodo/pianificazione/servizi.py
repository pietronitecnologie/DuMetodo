from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Exists, IntegerField, OuterRef, Q, QuerySet, Subquery, Sum
from django.db.models.functions import Coalesce
from django.template.loader import render_to_string
from django.utils import timezone

from metodo.anagrafica.models import Articolo, UnitaMisura
from metodo.comunicazioni import email
from metodo.comunicazioni.models import Nota
from metodo.configurazione import servizi as config
from metodo.configurazione.registro import REGISTRO
from metodo.core.models import Reparto
from metodo.core.permessi import F, ha_permesso
from metodo.ordini.models import Ordine, RigaOrdine, StatoOrdine, StatoRiga
from metodo.ordini.servizi import OperazioneNonConsentita

from . import calendario, piano, report_xlsx

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ----------------------------------------------------------------------------------------- soglie
@transaction.atomic
def aggiorna_soglia(reparto_id: int, valore: str, utente: Any) -> Reparto:
    if not ha_permesso(utente, F.PIANIFICAZIONE_CAPACITA):
        raise OperazioneNonConsentita("Non hai i permessi per modificare le capacità dei reparti.")
    testo = (valore or "").strip().replace(",", ".")
    try:
        soglia = Decimal(testo) if testo else None
    except InvalidOperation:
        raise OperazioneNonConsentita(f"Valore non valido: {valore!r}.") from None
    if soglia is not None and (not soglia.is_finite() or soglia < 0 or soglia > 100000):
        raise OperazioneNonConsentita("La capacità deve essere un numero di ore tra 0 e 100000.")
    reparto = Reparto.objects.select_for_update().get(pk=reparto_id)
    reparto.soglia_ore_settimanali = soglia
    reparto.save()
    return reparto


# ----------------------------------------------------------------------------------------- commesse bloccate
def commesse_bloccate(committenti: list[int] | None = None) -> list[dict[str, object]]:
    parole = [p.strip().lower() for p in config.leggi("pianificazione.parole_bloccanti").split(",") if p.strip()]
    if not parole:
        return []
    filtro = Q()
    for p in parole:
        filtro |= Q(testo__icontains=p)
    note = Nota.objects.visibili().filter(filtro)
    pezzi = (
        RigaOrdine.objects.filter(ordine=OuterRef("pk"), stato=StatoRiga.ATTIVA, bolla_emessa=False)
        .order_by()
        .values("ordine")
        .annotate(t=Sum("qta"))
        .values("t")
    )
    ordini = (
        Ordine.objects.filter(
            stato__in=[StatoOrdine.INSERITO, StatoOrdine.IN_LAVORAZIONE, StatoOrdine.EVASIONE_PARZIALE]
        )
        .filter(Exists(note.filter(ordine=OuterRef("pk"))))
        .annotate(pezzi=Subquery(pezzi))
        .order_by("data_scadenza", "anno", "serie", "numero", "appendice")
    )
    if committenti is not None:
        ordini = ordini.filter(committente__in=committenti)
    testi: dict[int, list[str]] = defaultdict(list)
    for ordine_id, testo in note.filter(ordine__in=ordini).order_by("creata_il").values_list("ordine_id", "testo"):
        testi[ordine_id].append(testo)
    return [
        {
            "ordine": o,
            "commessa": o.codice,
            "stato": o.get_stato_display(),
            "scadenza": o.data_scadenza,
            "pezzi": o.pezzi or Decimal(0),
            "note": " | ".join(testi[o.pk]),
        }
        for o in ordini
    ]


def genera_xlsx(
    adesso: dt.datetime | None = None, committenti: list[int] | None = None
) -> tuple[str, bytes, piano.Piano]:
    p = piano.calcola(adesso, committenti)
    return f"pianificazione_{p.generato_il:%Y%m%d}.xlsx", report_xlsx.genera(p, commesse_bloccate(committenti)), p


# ----------------------------------------------------------------------------------------- report email
def _destinatari(override: str | None, utente: Any) -> tuple[list[str], list[str]]:
    if override and override.strip():
        if utente is not None and not ha_permesso(utente, F.PIANIFICAZIONE_REPORT_LIBERO):
            raise OperazioneNonConsentita(
                "Non hai i permessi per inviare il report a indirizzi diversi da quelli configurati."
            )
        try:
            to = REGISTRO["email.report_produzione_to"].converti(override)
        except ValidationError as exc:
            raise OperazioneNonConsentita(" ".join(exc.messages)) from None
    else:
        to = config.leggi("email.report_produzione_to")
    if not to:
        raise OperazioneNonConsentita("Nessun destinatario: configura 'Report pianificazione: A' in Configurazione.")
    minuscoli = {t.lower() for t in to}
    cc = [c for c in config.leggi("email.report_produzione_cc") if c.lower() not in minuscoli]
    return to, cc


@transaction.atomic
def invia_report(utente: Any = None, *, destinatari: str | None = None, oggetto: str = "", messaggio: str = "") -> Any:
    """Accoda l'email con il report allegato. ``utente=None`` = invio automatico pianificato."""
    if utente is not None and not ha_permesso(utente, F.PIANIFICAZIONE):
        raise OperazioneNonConsentita("Non hai i permessi per inviare il report.")
    to, cc = _destinatari(destinatari, utente)
    oggetto = re.sub(r"[\r\n]+", " ", oggetto or "").strip()[:150] or "Report pianificazione produzione"
    nome, contenuto, p = genera_xlsx()
    contesto = {
        "piano": p,
        "messaggio": (messaggio or "").strip()[:2000],
        "url": settings.SITE_URL + "/pianificazione/",
    }
    mittente = config.leggi("email.mittente") or settings.DEFAULT_FROM_EMAIL
    return email.accoda(
        oggetto=oggetto,
        corpo_testo=render_to_string("pianificazione/email_report.txt", contesto),
        corpo_html=render_to_string("pianificazione/email_report.html", contesto),
        mittente=mittente,
        destinatari=to,
        cc=cc,
        rispondi_a=[mittente],
        riferimento=f"report-pianificazione:{p.generato_il:%Y%m%d}",
        allegati=[(nome, contenuto, XLSX)],
    )


# ----------------------------------------------------------------------------------------- agenda
STATI_AGENDA = (StatoOrdine.INSERITO, StatoOrdine.IN_LAVORAZIONE, StatoOrdine.EVASIONE_PARZIALE)


def _con_pezzi_e_note(qs: QuerySet[Ordine]) -> QuerySet[Ordine]:
    pezzi = (
        RigaOrdine.objects.filter(ordine=OuterRef("pk"), stato=StatoRiga.ATTIVA)
        .order_by()
        .values("ordine")
        .annotate(t=Sum("qta"))
        .values("t")
    )
    n_note = (
        Nota.objects.visibili()
        .filter(ordine=OuterRef("pk"))
        .order_by()
        .values("ordine")
        .annotate(n=Count("pk"))
        .values("n")
    )
    return qs.annotate(
        pezzi=Coalesce(Subquery(pezzi), Decimal(0)), n_note=Coalesce(Subquery(n_note, output_field=IntegerField()), 0)
    )


@dataclass
class Colonna:
    titolo: str
    ordini: list[Ordine]
    grandi: list[Ordine] | None = None
    piccole: list[Ordine] | None = None


def agenda(qs: QuerySet[Ordine], oggi: dt.date | None = None) -> list[Colonna]:
    oggi = oggi or timezone.localdate()
    lun = calendario.lunedi(oggi)
    limiti = [
        ("Arretrate", None, lun - dt.timedelta(days=1)),
        ("Questa settimana", lun, lun + dt.timedelta(days=6)),
        ("Prossima settimana", lun + dt.timedelta(weeks=1), lun + dt.timedelta(weeks=1, days=6)),
        ("Fra due settimane", lun + dt.timedelta(weeks=2), lun + dt.timedelta(weeks=2, days=6)),
    ]
    ordini = list(
        _con_pezzi_e_note(
            qs.filter(stato__in=STATI_AGENDA, data_scadenza__isnull=False, data_scadenza__lte=limiti[-1][2])
        ).order_by("data_scadenza", "numero")
    )
    colonne = []
    for titolo, dal, al in limiti:
        colonne.append(
            Colonna(
                f"{titolo}" + (f" ({dal:%d/%m} - {al:%d/%m})" if dal else ""),
                [o for o in ordini if (dal is None or o.data_scadenza >= dal) and o.data_scadenza <= al],
            )
        )
    return colonne


def agenda_pulsanti(qs: QuerySet[Ordine], oggi: dt.date | None = None) -> list[Colonna]:
    soglia = config.leggi("ordini.soglia_pezzi_commesse_grandi")
    colonne = agenda(qs.filter(appendice__icontains="T"), oggi)[:3]
    for c in colonne:
        c.grandi = [o for o in c.ordini if o.pezzi > soglia]
        c.piccole = [o for o in c.ordini if o.pezzi <= soglia]
    return colonne


# ----------------------------------------------------------------------------------------- tabulati
@dataclass
class VoceTabulato:
    articolo: str
    variante: str
    ordine: Ordine | None
    qta: Decimal
    ore: Decimal | None


def _ordini_tabulato(dal: dt.date, al: dt.date, stati: tuple[str, ...]) -> QuerySet[Ordine]:
    return Ordine.objects.filter(data_scadenza__range=(dal, al), stato__in=stati).exclude(appendice="")


def _voci(righe: QuerySet[RigaOrdine]) -> tuple[list[VoceTabulato], Decimal]:
    """Le righe a metri restano per ordine (cavi), quelle a pezzi si sommano per articolo/variante."""
    resa = config.leggi("produzione.prezzo_resa_oraria")
    um = dict(Articolo.objects.filter(codice__in=righe.values("articolo")).values_list("codice", "um"))
    gruppi: dict[tuple[str, str, int | None], list[RigaOrdine]] = defaultdict(list)
    for r in righe.select_related("ordine"):
        chiave_ordine = r.ordine_id if um.get(r.articolo) == UnitaMisura.METRI else None
        gruppi[(r.articolo, r.variante, chiave_ordine)].append(r)
    voci, totale = [], Decimal(0)
    for (articolo, variante, ordine_id), rr in sorted(gruppi.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        qta = sum((r.qta for r in rr), Decimal(0))
        ore = None
        if resa:
            ore = (sum((r.qta * (r.prezzo_unitario or 0) for r in rr), Decimal(0)) / resa).quantize(Decimal("0.01"))
            totale += ore
        voci.append(VoceTabulato(articolo, variante, rr[0].ordine if ordine_id else None, qta, ore))
    return voci, totale


def tabulato_settimanale(
    reparto: Reparto, lunedi: dt.date, committenti: list[int] | None = None
) -> tuple[list[VoceTabulato], Decimal]:
    dal, al = calendario.settimana(lunedi)
    ordini = _ordini_tabulato(dal, al, (StatoOrdine.INSERITO, StatoOrdine.EVASIONE_PARZIALE))
    if committenti is not None:
        ordini = ordini.filter(committente__in=committenti)
    righe = RigaOrdine.objects.attive().filter(
        ordine__in=ordini,
        bolla_emessa=False,
        data_scadenza__range=(dal, al),
        articolo__in=Articolo.objects.filter(reparto=reparto).values("codice"),
    )
    return _voci(righe)
