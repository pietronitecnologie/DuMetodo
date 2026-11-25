"""Reparti: gestiti da Metodo v2 (prima solo dall'admin di Django)."""

from django import forms
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .models import Reparto
from .permessi import F, richiede_permesso


class RepartoForm(forms.ModelForm):
    class Meta:
        model = Reparto
        fields = ["codice", "nome", "soglia_ore_settimanali", "gruppo_report", "committenti", "attivo"]
        widgets = {"committenti": forms.CheckboxSelectMultiple}
        help_texts = {
            "soglia_ore_settimanali": "Ore di lavoro disponibili in una settimana: base della pianificazione.",
        }

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        from metodo.committenti.models import Committente

        attivi = Committente.objects.filter(attivo=True)
        if attivi.count() > 1:
            self.fields["committenti"].queryset = attivi
            self.fields["committenti"].help_text = (
                "I clienti per cui lavora il reparto: nel piano e nei tabulati di un cliente compaiono solo i suoi "
                "reparti. Nessuno = tutti."
            )
        else:
            del self.fields["committenti"]  # con un solo cliente non serve

    def clean_codice(self) -> str:
        return self.cleaned_data["codice"].strip().upper()


@richiede_permesso(F.REPARTI)
def lista(request: HttpRequest) -> HttpResponse:
    return render(request, "anagrafiche/reparti.html", {"reparti": Reparto.objects.order_by("-attivo", "nome")})


@richiede_permesso(F.REPARTI)
def modifica(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    reparto = get_object_or_404(Reparto, pk=pk) if pk else None
    form = RepartoForm(request.POST or None, instance=reparto)
    if request.method == "POST" and form.is_valid():
        r = form.save()
        messages.success(request, f"Reparto {r.nome} salvato.")
        return redirect("reparti:lista")
    return render(
        request,
        "anagrafiche/modulo.html",
        {
            "form": form,
            "titolo": f"Reparto {reparto.nome}" if reparto else "Nuovo reparto",
            "indietro": reverse("reparti:lista"),
            "etichetta_indietro": "Reparti",
        },
    )
