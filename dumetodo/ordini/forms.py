from django import forms

from dumetodo.configurazione import servizi as config

from .models import StatoOrdine

CATEGORIE = [
    ("", "Tutte"),
    ("fast", "Fast"),
    ("express", "Express"),
    ("standard", "Standard"),
    ("pulsanti", "Pulsanti (T)"),
    ("elettronica", "Elettronica (R)"),
    ("contolavoro", "Conto lavoro (CL)"),
]


class FiltriOrdiniForm(forms.Form):
    q = forms.CharField(label="Ordine", required=False, max_length=30)
    articolo = forms.CharField(label="Articolo", required=False, max_length=50)
    anno = forms.IntegerField(label="Anno", required=False, min_value=2000, max_value=2100)
    stato = forms.MultipleChoiceField(
        label="Stato", required=False, choices=StatoOrdine.choices, widget=forms.CheckboxSelectMultiple
    )
    aperti = forms.BooleanField(label="Solo aperti", required=False, initial=True)
    categoria = forms.ChoiceField(label="Categoria", required=False, choices=CATEGORIE)
    scadenza_dal = forms.DateField(label="Scadenza dal", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    scadenza_al = forms.DateField(label="al", required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def filtra(self, qs):  # type: ignore[no-untyped-def]
        d = self.cleaned_data
        if d.get("q"):
            qs = qs.filter(numero__icontains=d["q"].strip())
        if d.get("articolo"):
            qs = qs.filter(righe__articolo__icontains=d["articolo"].strip(), righe__stato="ATT").distinct()
        if d.get("anno"):
            qs = qs.filter(anno=d["anno"])
        if d.get("stato"):
            qs = qs.filter(stato__in=d["stato"])
        elif d.get("aperti"):
            qs = qs.aperti()
        categoria = d.get("categoria")
        if categoria == "fast":
            qs = qs.filter(serie__endswith=config.leggi("ordini.serie_fast"))
        elif categoria == "express":
            qs = qs.filter(serie__endswith=config.leggi("ordini.serie_express"))
        elif categoria == "standard":
            qs = qs.exclude(serie__endswith=config.leggi("ordini.serie_fast")).exclude(
                serie__endswith=config.leggi("ordini.serie_express")
            )
        elif categoria == "pulsanti":
            qs = qs.filter(appendice__icontains="T")
        elif categoria == "elettronica":
            qs = qs.filter(appendice__icontains="R")
        elif categoria == "contolavoro":
            qs = qs.filter(appendice__iexact="CL")
        if d.get("scadenza_dal"):
            qs = qs.filter(data_scadenza__gte=d["scadenza_dal"])
        if d.get("scadenza_al"):
            qs = qs.filter(data_scadenza__lte=d["scadenza_al"])
        return qs


class DividiRigaForm(forms.Form):
    qta = forms.DecimalField(label="Quantità da separare", min_value=0, decimal_places=3, max_digits=14)
    nuova_scadenza = forms.DateField(
        label="Scadenza della nuova riga", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )


class CambiaScadenzaForm(forms.Form):
    nuova = forms.DateField(label="Nuova scadenza", widget=forms.DateInput(attrs={"type": "date"}))
    motivo = forms.CharField(label="Motivo", max_length=500, widget=forms.Textarea(attrs={"rows": 2}))


class AttivaProduzioneForm(forms.Form):
    dal = forms.DateField(label="Scadenza dal", widget=forms.DateInput(attrs={"type": "date"}))
    al = forms.DateField(label="al", widget=forms.DateInput(attrs={"type": "date"}))
