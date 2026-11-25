import re

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from metodo.core.permessi import F, ha_permesso, richiede_permesso
from metodo.ordini.servizi import OperazioneNonConsentita

from . import servizi
from .models import Committente


class CommittenteForm(forms.ModelForm):
    class Meta:
        model = Committente
        fields = ["codice", "nome", "colore", "attivo"]
        widgets = {"colore": forms.TextInput(attrs={"type": "color"})}

    def clean_codice(self) -> str:
        codice = self.cleaned_data["codice"].strip().upper()
        if not re.fullmatch(r"[A-Z0-9_-]{1,20}", codice):
            raise forms.ValidationError("Solo lettere, cifre, - e _ (senza spazi).")
        return codice

    def clean(self) -> dict:
        dati = super().clean()
        if self.instance.pk and self.instance.predefinito and dati.get("attivo") is False:
            raise forms.ValidationError("Il committente predefinito non si può disattivare: prima scegline un altro.")
        return dati


@richiede_permesso(F.COMMITTENTI)
def lista(request: HttpRequest) -> HttpResponse:
    if request.method == "POST" and request.POST.get("predefinito", "").isdigit():
        try:
            c = servizi.imposta_predefinito(request.user, int(request.POST["predefinito"]))
        except OperazioneNonConsentita as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{c} è ora il committente predefinito.")
        return redirect("committenti:lista")
    from django.db.models import Count

    qs = Committente.objects.annotate(n_ordini=Count("ordini")).order_by("-attivo", "nome")
    return render(request, "committenti/lista.html", {"committenti": qs})


@richiede_permesso(F.COMMITTENTI)
def modifica(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    c = get_object_or_404(Committente, pk=pk) if pk else None
    form = CommittenteForm(request.POST or None, instance=c)
    if request.method == "POST" and form.is_valid():
        c = form.save()
        messages.success(request, f"Committente {c} salvato.")
        return redirect("committenti:lista")
    return render(
        request,
        "anagrafiche/modulo.html",
        {
            "form": form,
            "titolo": f"Committente {c}" if c else "Nuovo committente",
            "indietro": reverse("committenti:lista"),
            "etichetta_indietro": "Clienti committenti",
        },
    )


@require_POST
@login_required
def scegli(request: HttpRequest) -> HttpResponse:
    """Selettore in alto: cambia il committente su cui si lavora (vuoto = tutti)."""
    valore = request.POST.get("committente", "")
    try:
        c = servizi.scegli(request, int(valore) if valore.isdigit() else None)
    except OperazioneNonConsentita as exc:
        messages.error(request, str(exc))
    else:
        messages.info(request, f"Stai lavorando su {c}." if c else "Stai vedendo tutti i committenti.")
    torna = request.POST.get("torna", "")
    if not url_has_allowed_host_and_scheme(
        torna, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        torna = "/"
    return redirect(torna)


def _gruppi_impostazioni(request: HttpRequest, c: Committente) -> list:  # type: ignore[type-arg]
    """Gruppi di parametri per committente che l'utente può modificare (i dati fiscali: solo con il permesso
    delle impostazioni del Gestionale). La casella del predefinito è quella di Configurazione."""
    from metodo.configurazione.registro import GRUPPI_GESTIONALE, PARAMETRI, Gruppo

    gruppi = []
    for g in Gruppo:
        if not any(p.gruppo == g and p.per_committente for p in PARAMETRI):
            continue
        if g in GRUPPI_GESTIONALE and not ha_permesso(request.user, F.GESTIONALE_IMPOSTAZIONI):
            continue
        if g == Gruppo.CASELLA and c.predefinito:
            continue
        gruppi.append(g)
    return gruppi


@richiede_permesso(F.COMMITTENTI)
def impostazioni(request: HttpRequest, pk: int) -> HttpResponse:
    """Parametri propri del committente: dati fiscali, pagamento, DDT, tracciati, email, casella ordini."""
    from django.core.exceptions import ValidationError

    from metodo.configurazione import servizi as config
    from metodo.configurazione.forms import ParametriCommittenteForm

    c = get_object_or_404(Committente, pk=pk)
    attivo = request.POST.get("gruppo") if request.method == "POST" else None
    forms_ = []
    for g in _gruppi_impostazioni(request, c):
        if attivo == g.name:
            form = ParametriCommittenteForm(g, c, request.POST)
            if form.is_valid():
                try:
                    config.scrivi_committente(
                        c, {p.chiave: form.cleaned_data[p.chiave] for p in form.parametri}, request.user
                    )
                except ValidationError as exc:
                    messages.error(request, " ".join(exc.messages))
                else:
                    for chiave in form.segreti_impostati:
                        if form.cleaned_data.get(f"{chiave}__cancella"):
                            config.cancella_segreto_committente(c, chiave, request.user)
                    messages.success(request, f"{g.value}: impostazioni di {c.nome} salvate.")
                    return redirect(
                        f"{reverse('committenti:impostazioni', args=[c.pk])}?aperto={g.name}#gruppo-{g.name}"
                    )
            else:
                messages.error(request, "Correggi gli errori evidenziati: nessun valore è stato salvato.")
        else:
            form = ParametriCommittenteForm(g, c)
        forms_.append(form)
    return render(
        request,
        "committenti/impostazioni.html",
        {"c": c, "forms": forms_, "aperto": request.GET.get("aperto", "")},
    )
