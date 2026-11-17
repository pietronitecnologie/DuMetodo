"""Form di inserimento e filtri delle liste.

I filtri delle liste arrivano in GET, così l'esportazione xlsx (stessi parametri + ``formato=xlsx``)
esporta esattamente ciò che si vede. In Metodo i filtri erano in POST e l'export in GET: si
esportava tutto. Gli intervalli di date comprendono il giorno finale.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django import forms
from django.db.models import Q, QuerySet
from django.utils import timezone

from dumetodo.core.models import Reparto
from dumetodo.ordini.models import Ordine

from . import servizi
from .models import (
    Test,
)

DATA = {"type": "date"}


def _campo_data(etichetta: str, required: bool = False, **kw: Any) -> forms.DateField:
    return forms.DateField(label=etichetta, required=required, widget=forms.DateInput(attrs=DATA), **kw)


def _non_futura(data: dt.date | None) -> dt.date | None:
    if data and data > timezone.localdate():
        raise forms.ValidationError("La data non può essere nel futuro.")
    return data


class CampoOrdine(forms.CharField):
    """Codice d'ordine scritto dall'utente -> ``Ordine`` (o ``None`` se vuoto e facoltativo)."""

    def __init__(self, **kw: Any) -> None:
        kw.setdefault("label", "Ordine")
        kw.setdefault("max_length", 60)
        kw.setdefault("help_text", f"Formato {servizi.FORMATO_ORDINE}.")
        super().__init__(**kw)

    def clean(self, value: Any) -> Ordine | None:  # type: ignore[override]
        testo = super().clean(value)
        if not testo:
            return None
        try:
            return servizi.risolvi_ordine(testo)
        except servizi.OperazioneNonConsentita as exc:
            raise forms.ValidationError(str(exc)) from exc


def _reparti() -> QuerySet[Reparto]:
    return Reparto.objects.filter(attivo=True)


class FormConStile(forms.Form):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            w = campo.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault("class", "form-check-input")
            elif isinstance(w, forms.RadioSelect):
                continue
            elif isinstance(w, forms.Select):
                w.attrs.setdefault("class", "form-select form-select-sm")
            else:
                w.attrs.setdefault("class", "form-control form-control-sm")


# =========================================================================== inserimento
class TracciaForm(FormConStile):
    codice = forms.CharField(label="Codice elemento", max_length=50)
    chiave = forms.CharField(label="Chiave", max_length=50)
    valore = forms.CharField(label="Valore", max_length=100, required=False)
    data = _campo_data("Data", required=True)
    reparto = forms.ModelChoiceField(label="Reparto", queryset=Reparto.objects.none())
    ordine = CampoOrdine(required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["reparto"].queryset = _reparti()

    def clean_data(self) -> dt.date | None:
        return _non_futura(self.cleaned_data.get("data"))


class ControlloQualitaForm(FormConStile):
    ordine = CampoOrdine(required=False)
    riferimento = forms.CharField(
        label="oppure Stock / riferimento", max_length=50, required=False, help_text="Se non è un ordine."
    )
    articolo = forms.CharField(label="Codice prodotto", max_length=50)
    qta_in_ordine = forms.DecimalField(
        label="Quantità in ordine", min_value=0, max_digits=14, decimal_places=3, required=False
    )
    data = _campo_data("Data", required=True)
    test = forms.ModelChoiceField(label="Test", queryset=Test.objects.none(), to_field_name="codice")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["test"].queryset = Test.objects.filter(attivo=True)

    def clean_data(self) -> dt.date | None:
        return _non_futura(self.cleaned_data.get("data"))

    def clean(self) -> dict[str, Any]:
        d = super().clean()
        if not self.errors and not d.get("ordine") and not d.get("riferimento"):
            raise forms.ValidationError("Indica l'ordine oppure il riferimento di stock.")
        return d


class Filtri(FormConStile):
    dal = _campo_data("Data dal")
    al = _campo_data("al")
    campo_data = "data__date"

    def clean(self) -> dict[str, Any]:
        d = super().clean()
        if d.get("dal") and d.get("al") and d["dal"] > d["al"]:
            raise forms.ValidationError("La data iniziale è successiva a quella finale.")
        return d

    def filtra_date(self, qs: QuerySet[Any], campo: str, dal: dt.date | None, al: dt.date | None) -> QuerySet[Any]:
        if dal:
            qs = qs.filter(**{f"{campo}__gte": dal})
        if al:
            qs = qs.filter(**{f"{campo}__lte": al})  # giorno finale compreso
        return qs

    def filtra(self, qs: QuerySet[Any]) -> QuerySet[Any]:
        d = self.cleaned_data
        return self.filtra_date(qs, self.campo_data, d.get("dal"), d.get("al"))


class FiltriTracce(Filtri):
    codice = forms.CharField(label="Codice", max_length=50, required=False)
    ordine = forms.CharField(label="Ordine (numero)", max_length=30, required=False)
    chiave = forms.CharField(label="Chiave", max_length=50, required=False)
    reparto = forms.ModelChoiceField(label="Reparto", queryset=Reparto.objects.none(), required=False)
    inserita_dal = _campo_data("Inserita dal")
    inserita_al = _campo_data("al")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["reparto"].queryset = Reparto.objects.all()

    def filtra(self, qs: QuerySet[Any]) -> QuerySet[Any]:
        qs = super().filtra(qs)
        d = self.cleaned_data
        if d.get("codice"):
            qs = qs.filter(elemento__codice__icontains=d["codice"].strip())
        if d.get("ordine"):
            qs = qs.filter(ordine__numero__icontains=d["ordine"].strip())
        if d.get("chiave"):
            qs = qs.filter(chiave__iexact=d["chiave"].strip())
        if d.get("reparto"):
            qs = qs.filter(reparto=d["reparto"])
        return self.filtra_date(qs, "inserita_il__date", d.get("inserita_dal"), d.get("inserita_al"))


class FiltriQualita(Filtri):
    campo_data = "controllo_qualita__data"
    ordine = forms.CharField(label="Ordine / stock", max_length=50, required=False)
    articolo = forms.CharField(label="Codice prodotto", max_length=50, required=False)
    test = forms.ModelChoiceField(label="Test", queryset=Test.objects.none(), required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["test"].queryset = Test.objects.all()

    def filtra(self, qs: QuerySet[Any]) -> QuerySet[Any]:
        qs = super().filtra(qs)
        d = self.cleaned_data
        if d.get("ordine"):
            t = d["ordine"].strip()
            qs = qs.filter(
                Q(controllo_qualita__ordine__numero__icontains=t) | Q(controllo_qualita__riferimento__icontains=t)
            )
        if d.get("articolo"):
            qs = qs.filter(controllo_qualita__articolo__icontains=d["articolo"].strip())
        if d.get("test"):
            qs = qs.filter(controllo_qualita__test=d["test"])
        return qs


class FiltriFile(Filtri):
    campo_data = "caricato_il__date"
    ordine = forms.CharField(label="Ordine (numero)", max_length=30, required=False)
    anno = forms.IntegerField(label="Anno", min_value=2000, max_value=2100, required=False)

    def filtra(self, qs: QuerySet[Any]) -> QuerySet[Any]:
        qs = super().filtra(qs)
        d = self.cleaned_data
        if d.get("ordine"):
            qs = qs.filter(ordine__numero__icontains=d["ordine"].strip())
        if d.get("anno"):
            qs = qs.filter(ordine__anno=d["anno"])
        return qs
