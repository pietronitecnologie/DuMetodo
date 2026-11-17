"""Form di inserimento e filtri delle liste.

I filtri delle liste arrivano in GET, così l'esportazione xlsx (stessi parametri + ``formato=xlsx``)
esporta esattamente ciò che si vede. In Metodo i filtri erano in POST e l'export in GET: si
esportava tutto. Gli intervalli di date comprendono il giorno finale.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django import forms
from django.db.models import QuerySet
from django.utils import timezone

from dumetodo.core.models import Reparto
from dumetodo.ordini.selezione import CampoOrdine

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
    ordine = CampoOrdine(required=False, help_text="")
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
            qs = qs.filter(ordine=d["ordine"])
        if d.get("chiave"):
            qs = qs.filter(chiave__iexact=d["chiave"].strip())
        if d.get("reparto"):
            qs = qs.filter(reparto=d["reparto"])
        return self.filtra_date(qs, "inserita_il__date", d.get("inserita_dal"), d.get("inserita_al"))


class FiltriQualita(Filtri):
    campo_data = "controllo_qualita__data"
    ordine = CampoOrdine(required=False, help_text="")
    riferimento = forms.CharField(label="Stock / riferimento", max_length=50, required=False)
    articolo = forms.CharField(label="Codice prodotto", max_length=50, required=False)
    test = forms.ModelChoiceField(label="Test", queryset=Test.objects.none(), required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["test"].queryset = Test.objects.all()

    def filtra(self, qs: QuerySet[Any]) -> QuerySet[Any]:
        qs = super().filtra(qs)
        d = self.cleaned_data
        if d.get("ordine"):
            qs = qs.filter(controllo_qualita__ordine=d["ordine"])
        if d.get("riferimento"):
            qs = qs.filter(controllo_qualita__riferimento__icontains=d["riferimento"].strip())
        if d.get("articolo"):
            qs = qs.filter(controllo_qualita__articolo__icontains=d["articolo"].strip())
        if d.get("test"):
            qs = qs.filter(controllo_qualita__test=d["test"])
        return qs


class FiltriFile(Filtri):
    campo_data = "caricato_il__date"
    ordine = CampoOrdine(required=False, help_text="")

    def filtra(self, qs: QuerySet[Any]) -> QuerySet[Any]:
        qs = super().filtra(qs)
        d = self.cleaned_data
        if d.get("ordine"):
            qs = qs.filter(ordine=d["ordine"])
        return qs


class CaricaFileForm(forms.Form):
    ordine = CampoOrdine(required=True)
