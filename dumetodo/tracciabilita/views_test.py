"""Test e controlli qualità: definiti da DuMetodo (prima solo dall'admin di Django)."""

from django import forms
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify

from dumetodo.core.permessi import F, richiede_permesso

from .models import Controllo, Test

# Controlli usati dal codice per riconoscere distinta e disegno: il codice non si cambia.
CODICI_FISSI = {"distinta", "disegno"}


def _codice_libero(modello: type, nome: str, codice: str, pk: int | None) -> str:
    base = slugify(codice or nome)[:50] or "voce"
    risultato, n = base, 2
    while modello.objects.filter(codice=risultato).exclude(pk=pk).exists():
        risultato = f"{base[:45]}-{n}"
        n += 1
    return risultato


class ControlloForm(forms.ModelForm):
    class Meta:
        model = Controllo
        fields = ["nome", "codice", "descrizione", "tipo", "presente_predefinito"]
        help_texts = {"codice": "Vuoto = ricavato dal nome. Identifica il controllo nei risultati e negli export."}

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["codice"].required = False
        if self.instance.pk and self.instance.codice in CODICI_FISSI:
            self.fields["codice"].disabled = True

    def clean_codice(self) -> str:
        if self.fields["codice"].disabled:
            return self.instance.codice
        d = self.cleaned_data
        return _codice_libero(Controllo, d.get("nome", ""), d.get("codice", ""), self.instance.pk)


class TestForm(forms.ModelForm):
    class Meta:
        model = Test
        fields = ["nome", "codice", "descrizione", "tipo", "attivo", "controlli"]
        widgets = {"controlli": forms.CheckboxSelectMultiple}
        help_texts = {"codice": "Vuoto = ricavato dal nome.", "controlli": "Le verifiche da eseguire con questo test."}

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["codice"].required = False
        self.fields["controlli"].queryset = Controllo.objects.order_by("tipo", "nome")

    def clean_codice(self) -> str:
        d = self.cleaned_data
        return _codice_libero(Test, d.get("nome", ""), d.get("codice", ""), self.instance.pk)


@richiede_permesso(F.QUALITA_TEST)
def lista(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "anagrafiche/test_qualita.html",
        {
            "test": Test.objects.prefetch_related("controlli").order_by("-attivo", "nome"),
            "controlli": Controllo.objects.order_by("tipo", "nome"),
        },
    )


def _modulo(request: HttpRequest, form_cls: type, istanza: object | None, titolo: str) -> HttpResponse:
    form = form_cls(request.POST or None, instance=istanza)
    if request.method == "POST" and form.is_valid():
        oggetto = form.save()
        messages.success(request, f"{oggetto} salvato.")
        return redirect("tracciabilita:test_lista")
    return render(
        request,
        "anagrafiche/modulo.html",
        {
            "form": form,
            "titolo": titolo,
            "indietro": reverse("tracciabilita:test_lista"),
            "etichetta_indietro": "Test qualità",
        },
    )


@richiede_permesso(F.QUALITA_TEST)
def test_modifica(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    t = get_object_or_404(Test, pk=pk) if pk else None
    return _modulo(request, TestForm, t, f"Test {t.nome}" if t else "Nuovo test")


@richiede_permesso(F.QUALITA_TEST)
def controllo_modifica(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    c = get_object_or_404(Controllo, pk=pk) if pk else None
    return _modulo(request, ControlloForm, c, f"Controllo {c.nome}" if c else "Nuovo controllo")
