"""Selezione univoca di un ordine nei form (al posto del codice scritto a mano).

L'ordine è identificato da serie + numero + appendice + anno. L'utente scrive quello che sa
(es. ``4300``, ``40 4300``, ``4300 T1 2026``) e sceglie da un elenco: il form riceve la chiave
primaria, quindi non serve conoscere il formato del codice. Per compatibilità (link, lettori di codici
a barre) il campo accetta anche il codice completo ``serie.numero[.appendice]-anno``.
"""

from __future__ import annotations

import re
from typing import Any

from django import forms
from django.db.models import Case, IntegerField, Q, QuerySet, Value, When
from django.urls import reverse

from .models import Ordine, StatoOrdine

MASSIMO_RISULTATI = 12
_PAROLA = re.compile(r"[A-Za-z0-9]+")
_CODICE = re.compile(
    r"^\s*(?P<serie>[0-9A-Za-z]{1,5})\.(?P<numero>[0-9A-Za-z]{1,30})(?:[.\-](?P<appendice>[A-Za-z][0-9A-Za-z]{0,9}))?"
    r"-(?P<anno>\d{4})\s*$"
)


def _senza_zeri(t: str) -> str:
    return t.lstrip("0") or "0" if t.isdigit() else t


def cerca(testo: str, limite: int = MASSIMO_RISULTATI, base: QuerySet[Ordine] | None = None) -> QuerySet[Ordine]:
    """Ordini che corrispondono a TUTTE le parole scritte (parte del numero, serie, appendice o anno).

    Senza testo: gli ordini aperti con la scadenza più vicina. Prima gli aperti, poi i più recenti.
    """
    base = base if base is not None else Ordine.objects.all()
    parole = _PAROLA.findall(testo or "")[:5]
    if not parole:
        return base.aperti().order_by("data_scadenza", "numero")[:limite]
    qs = base
    for p in parole:
        cond = Q(numero__icontains=_senza_zeri(p)) | Q(appendice__iexact=p)
        if p.isdigit():
            cond |= Q(serie__endswith=_senza_zeri(p))
            if len(p) == 4 and 2000 <= int(p) <= 2100:
                cond |= Q(anno=int(p))
        qs = qs.filter(cond)
    chiusi = [StatoOrdine.CONSEGNATO, StatoOrdine.ANNULLATO]
    return qs.annotate(
        chiuso=Case(When(stato__in=chiusi, then=Value(1)), default=Value(0), output_field=IntegerField())
    ).order_by("chiuso", "-anno", "numero", "appendice", "serie")[:limite]


def da_codice(testo: str, base: QuerySet[Ordine] | None = None) -> Ordine | None:
    """Ordine dal codice completo (lettore di codici a barre, link). None se non valido o inesistente.

    Il codice non dice il cliente committente: se esiste per più committenti vince quello predefinito.
    ``base`` limita la ricerca (es. ai committenti visibili all'utente)."""
    m = _CODICE.match(testo or "")
    if not m:
        return None
    numero, serie = m["numero"], m["serie"]
    numeri = {numero, numero.zfill(7)} if numero.isdigit() else {numero}
    serie_possibili = {serie, serie.zfill(5)} if serie.isdigit() else {serie}
    return (
        (base if base is not None else Ordine.objects.all())
        .filter(
            serie__in=serie_possibili, numero__in=numeri, appendice=(m["appendice"] or "").upper(), anno=int(m["anno"])
        )
        .order_by("-committente__predefinito", "committente__nome")
        .first()
    )


class WidgetOrdine(forms.Widget):
    """Campo di ricerca + elenco da cui scegliere; il valore inviato è la chiave primaria dell'ordine."""

    template_name = "widgets/ordine.html"

    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        super().__init__(attrs)

    def get_context(self, name: str, value: Any, attrs: dict[str, Any] | None) -> dict[str, Any]:
        ctx = super().get_context(name, value, attrs)
        ordine = value if isinstance(value, Ordine) else None
        if ordine is None and value not in (None, ""):
            testo = str(value)
            ordine = Ordine.objects.filter(pk=int(testo)).first() if testo.isdigit() else da_codice(testo)
        ctx["widget"].update(
            {
                "ordine": ordine,
                "pk": ordine.pk if ordine else "",
                "url": reverse("ordini:seleziona"),
                "obbligatorio": self.is_required,
            }
        )
        return ctx

    def value_from_datadict(self, data: Any, files: Any, name: str) -> Any:
        return data.get(name) or data.get(f"{name}_cerca") or None

    def value_omitted_from_data(self, data: Any, files: Any, name: str) -> bool:
        return name not in data and f"{name}_cerca" not in data


class CampoOrdine(forms.Field):
    """Restituisce un ``Ordine`` (o ``None`` se facoltativo e vuoto)."""

    widget = WidgetOrdine
    default_error_messages = {
        "non_trovato": "Ordine non trovato: scrivi il numero e sceglilo dall'elenco.",
    }

    def __init__(self, base: QuerySet[Ordine] | None = None, **kw: Any) -> None:
        self.base = base  # ordini tra cui cercare (None = tutti)
        kw.setdefault("label", "Ordine")
        kw.setdefault("help_text", "Scrivi numero, serie, appendice o anno e scegli dall'elenco.")
        super().__init__(**kw)

    def prepare_value(self, value: Any) -> Any:
        return value.pk if isinstance(value, Ordine) else value

    def to_python(self, value: Any) -> Ordine | None:
        if isinstance(value, Ordine):
            return value
        testo = str(value or "").strip()
        if not testo:
            return None
        base = self.base if self.base is not None else Ordine.objects.all()
        ordine = base.filter(pk=int(testo)).first() if testo.isdigit() else da_codice(testo, base)
        if ordine is None:
            raise forms.ValidationError(self.error_messages["non_trovato"], code="non_trovato")
        return ordine
