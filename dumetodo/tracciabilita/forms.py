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
    Chiave,
    EsitoDisplay,
    Postazione,
    ProdottoDisplay,
    Test,
    TestDisplay,
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
class BatteriaForm(FormConStile):
    azione = forms.ChoiceField(
        label="Azione",
        choices=[(Chiave.INGRESSO, "Primo ingresso"), (Chiave.RICARICA, "Ricarica")],
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
        initial=Chiave.INGRESSO,
    )
    codice = forms.CharField(
        label="Codice batteria",
        max_length=50,
        required=False,
        help_text="Primo ingresso: lascia vuoto per generarlo automaticamente.",
    )
    data = _campo_data("Data", required=True)
    reparto = forms.ModelChoiceField(label="Reparto", queryset=Reparto.objects.none())
    ordine = CampoOrdine(required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["reparto"].queryset = _reparti()

    def clean_data(self) -> dt.date | None:
        return _non_futura(self.cleaned_data.get("data"))


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


class CavoPostazioneForm(forms.Form):
    """Parametri dell'API dei banchi (nomi compatibili con ``insertTracciaCavi`` di Metodo)."""

    ordine = forms.CharField(max_length=60, required=False)
    serie = forms.CharField(max_length=5, required=False)
    numero = forms.CharField(max_length=30, required=False)
    appendice = forms.CharField(max_length=10, required=False)
    anno = forms.IntegerField(min_value=2000, max_value=2100, required=False)
    prodotto = forms.CharField(max_length=50)
    misura = forms.CharField(max_length=100)
    lotto = forms.CharField(max_length=100, required=False)
    lottoCavo = forms.CharField(max_length=100, required=False)
    data = forms.CharField(max_length=40)
    reparto = forms.CharField(max_length=30, required=False)
    location = forms.CharField(max_length=30, required=False)
    asset = forms.CharField(max_length=30, required=False)

    FORMATI_DATA = ("%Y/%m/%d,%H:%M:%S", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")

    def __init__(self, *args: Any, postazione: Postazione, **kwargs: Any) -> None:
        self.postazione = postazione
        super().__init__(*args, **kwargs)

    def clean_data(self) -> dt.datetime:
        testo = self.cleaned_data["data"].strip()
        for formato in self.FORMATI_DATA:
            try:
                valore = dt.datetime.strptime(testo, formato)
            except ValueError:
                continue
            valore = timezone.make_aware(valore)
            if not (dt.datetime(2000, 1, 1, tzinfo=dt.UTC) <= valore <= timezone.now() + dt.timedelta(days=1)):
                raise forms.ValidationError("Data fuori intervallo.")
            return valore
        raise forms.ValidationError("Data non valida: usa AAAA/MM/GG,hh:mm:ss oppure AAAA-MM-GGThh:mm:ss.")

    def clean(self) -> dict[str, Any]:
        d = super().clean()
        if self.errors:
            return d
        if d.get("ordine"):
            testo = d["ordine"]
            try:
                d["ordine"] = servizi.risolvi_ordine(testo)
            except servizi.OperazioneNonConsentita as exc:
                codice = "non_trovato" if servizi.FORMATO_RE.match(testo.strip()) else "non_valido"
                self.add_error("ordine", forms.ValidationError(str(exc), code=codice))
                return d
        elif d.get("serie") and d.get("numero") and d.get("anno"):
            d["ordine"] = servizi.cerca_ordine(d["serie"], d["numero"], d.get("appendice", ""), d["anno"])
            if d["ordine"] is None:
                self.add_error("ordine", forms.ValidationError("Ordine non trovato.", code="non_trovato"))
                return d
        else:
            self.add_error(
                "ordine", "Indica l'ordine: ordine=serie.numero[.appendice]-anno oppure serie, numero, anno."
            )
            return d
        codice_reparto = d.get("reparto") or d.get("location")
        d["reparto"] = None
        if codice_reparto:
            d["reparto"] = Reparto.objects.filter(codice=codice_reparto).first()
            if d["reparto"] is None:
                raise forms.ValidationError(f"Reparto {codice_reparto} sconosciuto.")
        if d.get("asset") and d["asset"] != self.postazione.codice:
            raise forms.ValidationError("Il parametro asset non corrisponde alla postazione del token.")
        d["lotto"] = d.get("lotto") or d.get("lottoCavo") or ""
        return d


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


class ReclamoForm(FormConStile):
    codice = forms.CharField(label="Identificativo", max_length=50)
    ordine = CampoOrdine(required=False)
    articolo = forms.CharField(label="Codice prodotto", max_length=50)
    reparto = forms.ModelChoiceField(label="Reparto", queryset=Reparto.objects.none())
    data = _campo_data("Data", required=True)
    note = forms.CharField(label="Note", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    file = forms.FileField(label="Allegato", required=False, help_text=f"Ammessi: {servizi.TIPI_AMMESSI}.")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["reparto"].queryset = _reparti()


class TestDisplayForm(forms.ModelForm):
    class Meta:
        model = TestDisplay
        fields = ["prodotto", "sito", "articolo", "esito", "fw", "lotto", "data", "note"]
        widgets = {"data": forms.DateInput(attrs=DATA), "note": forms.Textarea(attrs={"rows": 1})}

    def __init__(self, data: Any = None, *args: Any, **kwargs: Any) -> None:
        # Lettura "sito$$articolo" arrivata senza JavaScript: la si divide anche lato server.
        if data is not None and "$$" in data.get("sito", "") and not data.get("articolo"):
            data = data.copy()
            sito, _, articolo = data["sito"].partition("$$")
            data["sito"], data["articolo"] = sito, articolo
            if not data.get("esito"):
                data["esito"] = EsitoDisplay.POSITIVO
        super().__init__(data, *args, **kwargs)

    def clean_data(self) -> dt.date | None:
        return _non_futura(self.cleaned_data.get("data"))


# =========================================================================== filtri delle liste
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
    chiave = forms.CharField(label="Chiave / prodotto", max_length=50, required=False)
    lotto = forms.CharField(label="Lotto", max_length=100, required=False)
    reparto = forms.ModelChoiceField(label="Reparto", queryset=Reparto.objects.none(), required=False)
    postazione = forms.ModelChoiceField(label="Postazione", queryset=Postazione.objects.none(), required=False)
    inserita_dal = _campo_data("Inserita dal")
    inserita_al = _campo_data("al")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["reparto"].queryset = Reparto.objects.all()
        self.fields["postazione"].queryset = Postazione.objects.all()

    def filtra(self, qs: QuerySet[Any]) -> QuerySet[Any]:
        qs = super().filtra(qs)
        d = self.cleaned_data
        if d.get("codice"):
            qs = qs.filter(elemento__codice__icontains=d["codice"].strip())
        if d.get("ordine"):
            qs = qs.filter(ordine__numero__icontains=d["ordine"].strip())
        if d.get("chiave"):
            qs = qs.filter(chiave__iexact=d["chiave"].strip())
        if d.get("lotto"):
            qs = qs.filter(elemento__lotto__icontains=d["lotto"].strip())
        if d.get("reparto"):
            qs = qs.filter(reparto=d["reparto"])
        if d.get("postazione"):
            qs = qs.filter(postazione=d["postazione"])
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


class FiltriReclami(Filtri):
    campo_data = "data"
    codice = forms.CharField(label="Identificativo", max_length=50, required=False)
    ordine = forms.CharField(label="Ordine (numero)", max_length=50, required=False)
    articolo = forms.CharField(label="Codice prodotto", max_length=50, required=False)
    reparto = forms.ModelChoiceField(label="Reparto", queryset=Reparto.objects.none(), required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["reparto"].queryset = Reparto.objects.all()

    def filtra(self, qs: QuerySet[Any]) -> QuerySet[Any]:
        qs = super().filtra(qs)
        d = self.cleaned_data
        if d.get("codice"):
            qs = qs.filter(codice__icontains=d["codice"].strip())
        if d.get("ordine"):
            t = d["ordine"].strip()
            qs = qs.filter(Q(ordine__numero__icontains=t) | Q(riferimento_ordine__icontains=t))
        if d.get("articolo"):
            qs = qs.filter(articolo__icontains=d["articolo"].strip())
        if d.get("reparto"):
            qs = qs.filter(reparto=d["reparto"])
        return qs


class FiltriDisplay(Filtri):
    campo_data = "data"
    prodotto = forms.ChoiceField(label="Prodotto", choices=[("", "Tutti"), *ProdottoDisplay.choices], required=False)
    sito = forms.CharField(label="Sito", max_length=100, required=False)
    articolo = forms.CharField(label="Articolo", max_length=100, required=False)
    esito = forms.ChoiceField(label="Esito", choices=[("", "Tutti"), *EsitoDisplay.choices], required=False)

    def filtra(self, qs: QuerySet[Any]) -> QuerySet[Any]:
        qs = super().filtra(qs)
        d = self.cleaned_data
        if d.get("prodotto"):
            qs = qs.filter(prodotto=d["prodotto"])
        if d.get("sito"):
            qs = qs.filter(sito__icontains=d["sito"].strip())
        if d.get("articolo"):
            qs = qs.filter(articolo__icontains=d["articolo"].strip())
        if d.get("esito"):
            qs = qs.filter(esito=d["esito"])
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
