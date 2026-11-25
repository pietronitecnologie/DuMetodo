from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from typing import Any

import nh3
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from metodo.consegne.models import DDT
from metodo.core.permessi import F, ha_permesso
from metodo.ordini.models import Ordine, OrigineModificaScadenza, RigaOrdine, StatoRiga
from metodo.ordini.servizi import OperazioneNonConsentita, registra_scadenza

from .models import Annuncio

TAG_AMMESSI = {"b", "strong", "i", "em", "u", "p", "br", "ul", "ol", "li", "a"}


def _richiedi(utente: Any, codice: str) -> None:
    if not ha_permesso(utente, codice):
        raise OperazioneNonConsentita("Non hai i permessi per questa operazione.")


# ----------------------------------------------------------------------------------------- annunci
def pulisci_html(html: str) -> str:
    """Solo tag di formattazione e link http/https: nessuno script, stile o attributo pericoloso."""
    return nh3.clean(
        html or "",
        tags=TAG_AMMESSI,
        attributes={"a": {"href"}},
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
    ).strip()


@transaction.atomic
def salva_annuncio(
    utente: Any, annuncio: Annuncio | None, *, data: dt.date, testo: str, attivo: bool, permanente: bool
) -> Annuncio:
    _richiedi(utente, F.UTILITA_ANNUNCI)
    testo = pulisci_html(testo)
    if not testo:
        raise OperazioneNonConsentita("Il testo dell'annuncio è vuoto.")
    a = annuncio or Annuncio(creato_da=utente)
    a.data, a.testo, a.permanente = data, testo, permanente
    _imposta_attivo(a, attivo)
    a.save()
    return a


def _imposta_attivo(a: Annuncio, attivo: bool) -> None:
    if attivo and (not a.attivo or a.attivato_il is None or a.scaduto):
        a.attivato_il = timezone.now()  # riattivare un annuncio scaduto lo rimette in vista per un giorno
    if not attivo:
        a.attivato_il = None
    a.attivo = attivo


@transaction.atomic
def attiva_disattiva(utente: Any, annuncio_id: int) -> Annuncio:
    _richiedi(utente, F.UTILITA_ANNUNCI)
    a = Annuncio.objects.select_for_update().get(pk=annuncio_id)
    _imposta_attivo(a, not a.attivo or a.scaduto)
    a.save()
    return a


# ----------------------------------------------------------------------------------------- ristampa etichette
def ordini_per_ddt(numero: str, anno: int, committenti: list[int] | None = None) -> QuerySet[Ordine]:
    qs = Ordine.objects.all() if committenti is None else Ordine.objects.filter(committente__in=committenti)
    return (
        qs.filter(righe__consegnata__ddt__numero=numero.strip(), righe__consegnata__ddt__anno=anno)
        .distinct()
        .order_by("serie", "numero", "appendice")
    )


def anni_del_ddt(numero: str) -> list[int]:
    return list(DDT.objects.filter(numero=numero.strip()).order_by("-anno").values_list("anno", flat=True))


# ----------------------------------------------------------------------------------------- scorporazione di massa
def righe_consegnate(
    articolo: str, variante: str, dal: dt.date, al: dt.date, committenti: list[int] | None = None
) -> QuerySet[RigaOrdine]:
    qs = RigaOrdine.objects.filter(bolla_emessa=True, data_consegna__range=(dal, al)).select_related("ordine")
    if committenti is not None:
        qs = qs.filter(ordine__committente__in=committenti)
    if articolo.strip():
        qs = qs.filter(articolo__icontains=articolo.strip())
    if variante.strip():
        qs = qs.filter(variante__icontains=variante.strip())
    return qs.order_by("-data_consegna", "ordine__numero", "n_riga")


@transaction.atomic
def scorpora_massa(utente: Any, visualizzate: Iterable[int], da_scorporare: Iterable[int]) -> int:
    """Imposta 'scorporata' sulle righe visualizzate: selezionate = scorporate, le altre no.
    Agisce solo su righe consegnate e attive (in Metodo si poteva inviare qualunque id)."""
    _richiedi(utente, F.UTILITA_SCORPORAZIONE)
    ids, scelte = set(visualizzate), set(da_scorporare)
    if not scelte <= ids:
        raise OperazioneNonConsentita("Selezione non valida.")
    righe = RigaOrdine.objects.select_for_update().filter(pk__in=ids, bolla_emessa=True, stato=StatoRiga.ATTIVA)
    modificate = 0
    for r in righe:
        nuovo = r.pk in scelte
        if r.scorporata != nuovo:
            r.scorporata = nuovo
            r.save(update_fields=["scorporata", "aggiornato_il"])
            modificate += 1
    return modificate


# ----------------------------------------------------------------------------------------- cambia ordine
@transaction.atomic
def cambia_ordine(
    utente: Any, ordine_id: int, *, numero: str, serie: str, appendice: str, data_scadenza: dt.date | None, motivo: str
) -> Ordine:
    """Cambia numero/serie/appendice (anno fisso) e scadenza di un ordine.

    Con le ForeignKey tutte le righe, note, consegne, DDT e tracce restano collegate: in Metodo
    venivano aggiornate solo le righe e il resto (DDT, file, ticket, controlli qualità) restava orfano."""
    _richiedi(utente, F.UTILITA_CAMBIA_ORDINE)
    numero, serie = numero.strip(), serie.strip()
    appendice = appendice.strip().upper()
    appendice = "" if appendice == "0" else appendice
    if not numero or not serie:
        raise OperazioneNonConsentita("Numero e serie sono obbligatori.")
    if len(numero) > 30 or len(serie) > 5 or len(appendice) > 10:
        raise OperazioneNonConsentita("Numero (max 30), serie (max 5) o appendice (max 10) troppo lunghi.")
    ordine = Ordine.objects.select_for_update().get(pk=ordine_id)
    if (
        Ordine.objects.per_chiave(serie, numero, appendice, ordine.anno)
        .filter(committente_id=ordine.committente_id)
        .exclude(pk=ordine.pk)
        .exists()
    ):
        raise OperazioneNonConsentita(
            f"Esiste già l'ordine {serie}.{numero}{'.' + appendice if appendice else ''}-{ordine.anno}."
        )
    vecchio = ordine.codice
    ordine.numero, ordine.serie, ordine.appendice = numero, serie, appendice
    if data_scadenza and data_scadenza != ordine.data_scadenza:
        registra_scadenza(
            ordine,
            ordine.data_scadenza,
            data_scadenza,
            OrigineModificaScadenza.UTENTE,
            utente=utente,
            motivo=motivo.strip() or f"Cambia ordine (era {vecchio})",
        )
        ordine.data_scadenza = data_scadenza
        ordine.righe.attive().filter(bolla_emessa=False).update(data_scadenza=data_scadenza)
    try:
        with transaction.atomic():
            ordine.save()
    except IntegrityError as exc:
        raise OperazioneNonConsentita("Esiste già un ordine con questi dati.") from exc
    return ordine


def ordini_correlati(ordine: Ordine) -> dict[str, int]:
    return {
        "righe": ordine.righe.count(),
        "note": ordine.note.count(),
        "righe consegnate": RigaOrdine.objects.filter(ordine=ordine, bolla_emessa=True).count(),
    }


def ids_da_post(valori: Sequence[str]) -> list[int]:
    return [int(v) for v in valori if v.isdigit()]
