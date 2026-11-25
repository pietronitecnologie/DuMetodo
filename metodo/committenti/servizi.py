"""Quali committenti vede un utente e su quale sta lavorando (committente corrente, in sessione).

- Utente del Cliente: solo i committenti assegnati nel suo profilo (nessuno = niente, salvo che ci sia un solo
  committente: allora è quello, come in Metodo v2).
- Utente interno: i committenti assegnati; se non ne ha, tutti quelli attivi.
- Committente corrente: scelto dal selettore in alto; ``None`` = «Tutti» (solo con più committenti).
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import QuerySet

from metodo.core.permessi import F, ha_permesso, is_cliente
from metodo.ordini.servizi import OperazioneNonConsentita

from .models import Committente

CHIAVE_SESSIONE = "committente"


def assegnati(utente: Any) -> QuerySet[Committente]:
    profilo = getattr(utente, "profilo_metodo", None) if getattr(utente, "pk", None) else None
    if profilo is None:
        return Committente.objects.none()
    return profilo.committenti.all()


def accessibili(utente: Any) -> QuerySet[Committente]:
    if not getattr(utente, "is_authenticated", False):
        return Committente.objects.none()
    cache = getattr(utente, "_dm_committenti", None)
    if cache is None:
        propri = assegnati(utente)
        attivi = Committente.objects.filter(attivo=True)
        if propri.exists():
            qs = attivi.filter(pk__in=propri.values("pk"))
        elif is_cliente(utente) and attivi.count() > 1:
            qs = Committente.objects.none()  # con più committenti l'utente del Cliente vede solo i suoi
        else:
            qs = attivi  # interni senza assegnazioni; o un solo committente: nessuna differenza con Metodo v2
        cache = list(qs.order_by("nome"))
        utente._dm_committenti = cache
    return Committente.objects.filter(pk__in=[c.pk for c in cache]).order_by("nome")


def corrente(request: Any) -> Committente | None:
    """Committente su cui l'utente sta lavorando; ``None`` = tutti i suoi committenti."""
    elenco = list(accessibili(request.user))
    if len(elenco) == 1:
        return elenco[0]
    pk = request.session.get(CHIAVE_SESSIONE) if hasattr(request, "session") else None
    return next((c for c in elenco if c.pk == pk), None)


def scegli(request: Any, committente_id: int | None) -> Committente | None:
    if committente_id is None:
        request.session.pop(CHIAVE_SESSIONE, None)
        return None
    c = accessibili(request.user).filter(pk=committente_id).first()
    if c is None:
        raise OperazioneNonConsentita("Committente non disponibile.")
    request.session[CHIAVE_SESSIONE] = c.pk
    return c


def filtra(qs: QuerySet[Any], request: Any, campo: str = "committente") -> QuerySet[Any]:
    """Limita un queryset al committente corrente (o, con «Tutti», ai committenti dell'utente)."""
    c = corrente(request)
    if c is not None:
        return qs.filter(**{campo: c.pk})
    return qs.filter(**{f"{campo}__in": accessibili(request.user)})


def puo_vedere(request: Any, committente_id: int) -> bool:
    return accessibili(request.user).filter(pk=committente_id).exists()


def predefinito() -> Committente:
    from .models import predefinito_id

    return Committente.objects.get(pk=predefinito_id())


@transaction.atomic
def imposta_predefinito(utente: Any, committente_id: int) -> Committente:
    if not ha_permesso(utente, F.COMMITTENTI):
        raise OperazioneNonConsentita("Non hai i permessi per questa operazione.")
    c = Committente.objects.select_for_update().get(pk=committente_id)
    if not c.attivo:
        raise OperazioneNonConsentita("Un committente disattivato non può essere il predefinito.")
    Committente.objects.filter(predefinito=True).exclude(pk=c.pk).update(predefinito=False)
    c.predefinito = True
    c.save(update_fields=["predefinito", "aggiornato_il"])
    return c


def ordine_o_404(request: Any, pk: int, qs: Any = None) -> Any:
    """L'ordine, solo se è di un committente che l'utente può vedere (altrimenti 404, come se non esistesse)."""
    from django.shortcuts import get_object_or_404

    from metodo.ordini.models import Ordine

    return get_object_or_404(
        (qs if qs is not None else Ordine.objects).filter(committente__in=accessibili(request.user)), pk=pk
    )
