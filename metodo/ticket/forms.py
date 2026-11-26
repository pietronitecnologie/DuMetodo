from __future__ import annotations

from typing import Any

from django import forms
from django.db.models import Q, QuerySet

from metodo.ordini.models import Ordine, RigaOrdine
from metodo.ordini.selezione import CampoOrdine

from . import servizi
from .models import Categoria, Priorita, Stato, Ticket


class CampoUtente(forms.ModelChoiceField):
    def label_from_instance(self, obj: Any) -> str:
        return servizi.nome(obj)


class NuovoTicketForm(forms.ModelForm):
    ordine = CampoOrdine(required=False, help_text="Facoltativo: l'ordine a cui si riferisce il ticket.")
    assegnato_a = CampoUtente(queryset=None, required=False, label="Assegna a", empty_label="— nessuno —")

    class Meta:
        model = Ticket
        fields = ["titolo", "categoria", "priorita", "ordine", "riga", "descrizione", "assegnato_a"]
        widgets = {"descrizione": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args: Any, ordine: Ordine | None = None, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.fields["assegnato_a"].queryset = servizi.incaricabili()
        ordine = ordine or self._ordine_scritto()
        righe: QuerySet[RigaOrdine] = RigaOrdine.objects.none()
        if ordine is not None:
            self.fields["ordine"].initial = ordine
            righe = RigaOrdine.objects.filter(ordine=ordine).order_by("n_riga")
        self.fields["riga"].queryset = righe
        self.fields["riga"].required = False
        self.fields["riga"].empty_label = "— tutto l'ordine —"
        self.fields["riga"].label_from_instance = lambda r: f"{r.n_riga} · {r.articolo} × {r.qta.normalize():f}"
        self.fields["riga"].help_text = "Facoltativa: la riga dell'ordine interessata."
        for nome, campo in self.fields.items():
            if nome != "ordine":
                classe = "form-select" if isinstance(campo.widget, forms.Select) else "form-control"
                campo.widget.attrs.setdefault("class", classe)

    def _ordine_scritto(self) -> Ordine | None:
        valore = self.data.get("ordine") if self.is_bound else None
        try:
            return self.fields["ordine"].to_python(valore) if valore else None
        except forms.ValidationError:
            return None


class CommentoForm(forms.Form):
    testo = forms.CharField(label="Commento", widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}))


STATI_FILTRO = [("aperti", "Non chiusi"), ("", "Tutti"), *Stato.choices, ("ritardo", "In ritardo")]


class FiltriTicketForm(forms.Form):
    q = forms.CharField(
        required=False, label="Cerca", widget=forms.TextInput(attrs={"placeholder": "N°, titolo, testo…"})
    )
    ordine = forms.CharField(required=False, label="Ordine", widget=forms.TextInput(attrs={"placeholder": "Numero"}))
    stato = forms.ChoiceField(required=False, choices=STATI_FILTRO, initial="aperti")
    categoria = forms.ChoiceField(required=False, choices=[("", "Tutte"), *Categoria.choices])
    priorita = forms.ChoiceField(required=False, label="Priorità", choices=[("", "Tutte"), *Priorita.choices])
    chi = forms.ChoiceField(
        required=False,
        label="Di chi",
        choices=[
            ("", "Tutti"),
            ("miei", "Assegnati a me"),
            ("aperti_da_me", "Aperti da me"),
            ("liberi", "Non assegnati"),
        ],
    )

    def __init__(self, *args: Any, **kw: Any) -> None:
        super().__init__(*args, **kw)
        for campo in self.fields.values():
            sel = isinstance(campo.widget, forms.Select)
            campo.widget.attrs.setdefault(
                "class", "form-select form-select-sm" if sel else "form-control form-control-sm"
            )

    def filtra(self, qs: QuerySet[Ticket], utente: Any) -> tuple[QuerySet[Ticket], bool]:
        """(ticket filtrati, solo_in_ritardo): il ritardo dipende dalle ore, si filtra dopo il calcolo."""
        d = self.cleaned_data if self.is_valid() else {"stato": "aperti"}
        stato = d.get("stato", "aperti") if self.is_bound else "aperti"
        if stato in ("aperti", "ritardo"):
            qs = qs.aperti()
        elif stato:
            qs = qs.filter(stato=stato)
        if q := (d.get("q") or "").strip():
            filtro = Q(titolo__icontains=q) | Q(descrizione__icontains=q) | Q(eventi__testo__icontains=q)
            if q.lstrip("#").isdigit():
                filtro |= Q(pk=int(q.lstrip("#")))
            qs = qs.filter(filtro).distinct()
        if o := (d.get("ordine") or "").strip():
            qs = qs.filter(ordine__codice__icontains=o)
        if d.get("categoria"):
            qs = qs.filter(categoria=d["categoria"])
        if d.get("priorita"):
            qs = qs.filter(priorita=d["priorita"])
        match d.get("chi"):
            case "miei":
                qs = qs.filter(assegnato_a=utente)
            case "aperti_da_me":
                qs = qs.filter(aperto_da=utente)
            case "liberi":
                qs = qs.filter(assegnato_a__isnull=True)
        return qs, stato == "ritardo"
