from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from metodo.committenti import servizi as committenti
from metodo.core.permessi import F, richiede_permesso

from . import posta, servizi
from .models import FileImportato, ProblemaImportazione, StatoCasella

MAX_FILE_CARICATI = 20


class CaricamentoForm(forms.Form):
    def clean(self):  # type: ignore[no-untyped-def]
        files = self.files.getlist("file")
        if not files:
            raise forms.ValidationError("Seleziona almeno un file .txt.")
        if len(files) > MAX_FILE_CARICATI:
            raise forms.ValidationError(f"Massimo {MAX_FILE_CARICATI} file per volta.")
        for f in files:
            if not f.name.lower().endswith(".txt"):
                raise forms.ValidationError(f"{f.name}: sono ammessi solo file .txt.")
            if f.size > 5 * 1024 * 1024:
                raise forms.ValidationError(f"{f.name}: file troppo grande.")
        return {"file": files}


@richiede_permesso(F.IMPORTAZIONE)
def pagina(request: HttpRequest) -> HttpResponse:
    form = CaricamentoForm(request.POST or None, request.FILES or None)
    scelta = committenti.accessibili(request.user)
    risultati = []
    if request.method == "POST" and form.is_valid():
        # Di chi sono gli ordini del file: scelto nel modulo (con più committenti), altrimenti quello corrente.
        valore = request.POST.get("committente", "")
        committente = scelta.filter(pk=int(valore)).first() if valore.isdigit() else committenti.corrente(request)
        if committente is None:
            messages.error(request, "Scegli il cliente committente degli ordini del file.")
        else:
            for f in form.cleaned_data["file"]:
                risultati.append(servizi.importa_caricamento(f.name, f.read(), request.user, committente))
    pagina_file = Paginator(
        committenti.filtra(
            FileImportato.objects.select_related("messaggio", "caricato_da", "committente"), request
        ).defer("contenuto"),
        30,
    )
    return render(
        request,
        "importazione/pagina.html",
        {
            "form": form,
            "risultati": risultati,
            "pagina": pagina_file.get_page(request.GET.get("pagina")),
            "problemi_aperti": committenti.filtra(
                ProblemaImportazione.objects.filter(gestito=False), request, "file__committente"
            ).count(),
            "committenti_caricamento": scelta if scelta.count() > 1 else None,
            "committente_corrente": committenti.corrente(request),
            "caselle": StatoCasella.objects.all(),
            "casella_configurata": posta.casella_configurata(),
        },
    )


@richiede_permesso(F.IMPORTAZIONE, F.UTILITA_ERRORI_IMPORTAZIONE)
def dettaglio_file(request: HttpRequest, pk: int) -> HttpResponse:
    file = committenti.oggetto_o_404(request, FileImportato.objects.defer("contenuto"), pk)
    return render(
        request,
        "importazione/dettaglio_file.html",
        {
            "file": file,
            "problemi": file.problemi.select_related("ordine", "gestito_da"),
        },
    )


@richiede_permesso(F.IMPORTAZIONE, F.UTILITA_ERRORI_IMPORTAZIONE)
def scarica_file(request: HttpRequest, pk: int) -> HttpResponse:
    file = committenti.oggetto_o_404(request, FileImportato.objects.all(), pk)
    risposta = HttpResponse(bytes(file.contenuto), content_type="text/plain; charset=latin-1")
    nome = file.nome.replace("/", "_").replace('"', "")
    risposta["Content-Disposition"] = f'attachment; filename="{nome}"'
    return risposta


@require_POST
@richiede_permesso(F.IMPORTAZIONE_AVANZATA)
def rielabora(request: HttpRequest, pk: int) -> HttpResponse:
    file = committenti.oggetto_o_404(request, FileImportato.objects.all(), pk)
    file = servizi.importa(file, forza=True)
    messages.success(request, f"File rielaborato: {file.get_stato_display()}.")
    return redirect("importazione:dettaglio_file", pk=pk)


@richiede_permesso(F.IMPORTAZIONE, F.UTILITA_ERRORI_IMPORTAZIONE)
def problemi(request: HttpRequest) -> HttpResponse:
    qs = committenti.filtra(
        ProblemaImportazione.objects.select_related("file", "ordine").filter(gestito=request.GET.get("gestiti") == "1"),
        request,
        "file__committente",
    )
    return render(
        request,
        "importazione/problemi.html",
        {
            "pagina": Paginator(qs, 50).get_page(request.GET.get("pagina")),
            "gestiti": request.GET.get("gestiti") == "1",
        },
    )


@require_POST
@richiede_permesso(F.IMPORTAZIONE, F.UTILITA_ERRORI_IMPORTAZIONE)
def segna_gestito(request: HttpRequest, pk: int) -> HttpResponse:
    committenti.filtra(ProblemaImportazione.objects.filter(pk=pk, gestito=False), request, "file__committente").update(
        gestito=True, gestito_da=request.user, gestito_il=timezone.now()
    )
    torna = request.POST.get("torna", "")
    if url_has_allowed_host_and_scheme(torna, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(torna)
    return redirect("importazione:problemi")


@require_POST
@richiede_permesso(F.IMPORTAZIONE_AVANZATA)
def leggi_casella_ora(request: HttpRequest) -> HttpResponse:
    from .tasks import leggi_casella_ordini

    leggi_casella_ordini.delay()
    messages.info(request, "Lettura della casella avviata: i risultati compariranno qui tra poco.")
    return redirect("importazione:pagina")
