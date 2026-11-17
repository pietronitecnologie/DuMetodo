from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from dumetodo.core.ruoli import Ruolo, richiede_ruolo

from . import servizi
from .forms import ParametriGruppoForm
from .models import ValoreParametro
from .registro import Gruppo


@richiede_ruolo(Ruolo.AMMINISTRATORE)
@require_http_methods(["GET", "POST"])
def pagina(request: HttpRequest) -> HttpResponse:
    gruppo_attivo = request.POST.get("gruppo")
    forms = []
    for gruppo in Gruppo:
        if request.method == "POST" and gruppo.name == gruppo_attivo:
            form = ParametriGruppoForm(gruppo, request.POST)
            if form.is_valid():
                servizi.scrivi_molti({p.chiave: form.cleaned_data[p.chiave] for p in form.parametri}, request.user)
                messages.success(request, f"Parametri '{gruppo.value}' salvati.")
                return redirect("configurazione:pagina")
            messages.error(request, "Correggi gli errori evidenziati: nessun valore è stato salvato.")
        else:
            form = ParametriGruppoForm(gruppo)
        forms.append(form)
    from dumetodo.tracciati.esempi import READYPRO_BOLLA

    return render(request, "configurazione/pagina.html", {"forms": forms, "esempio_readypro": READYPRO_BOLLA})


@richiede_ruolo(Ruolo.AMMINISTRATORE)
def storico(request: HttpRequest) -> HttpResponse:
    voci = ValoreParametro.history.select_related("history_user").order_by("-history_date")[:200]
    return render(request, "configurazione/storico.html", {"voci": voci})


@richiede_ruolo(Ruolo.AMMINISTRATORE)
@require_http_methods(["POST"])
def anteprima_tracciato(request: HttpRequest) -> HttpResponse:
    """Genera il file d'esempio con il tracciato scritto nel form (non ancora salvato)."""
    from dumetodo.tracciati import esempi, linguaggio

    sorgente = next((v for k, v in request.POST.items() if k.endswith("tracciato_bolla")), "")
    contesto: dict[str, object] = {}
    try:
        tracciato = linguaggio.compila(sorgente)
        file = linguaggio.genera(tracciato, esempi.DOCUMENTO_ESEMPIO, esempi.ORDINI_ESEMPIO)
        contesto = {"file": file, "visibile": file.testo.replace("\r\n", "⏎\n").replace("\t", "→")}
    except linguaggio.ErroreTracciato as exc:
        contesto = {"errore": str(exc)}
    return render(request, "configurazione/_anteprima_tracciato.html", contesto)


@richiede_ruolo(Ruolo.AMMINISTRATORE)
def guida_tracciati(request: HttpRequest) -> HttpResponse:
    from dumetodo.tracciati import esempi, linguaggio

    return render(
        request,
        "configurazione/guida_tracciati.html",
        {
            "campi": linguaggio.CAMPI,
            "sezioni": linguaggio.SEZIONI,
            "filtri": linguaggio.FILTRI,
            "impostazioni": linguaggio.IMPOSTAZIONI,
            "esempio": esempi.READYPRO_BOLLA,
        },
    )
