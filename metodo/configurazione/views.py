from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseNotModified
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from metodo.core.permessi import F, richiede_permesso

from . import servizi
from .forms import ParametriGruppoForm
from .models import ValoreParametro
from .registro import GRUPPI_GESTIONALE, Gruppo


@richiede_permesso(F.CONFIGURAZIONE)
@require_http_methods(["GET", "POST"])
def pagina(request: HttpRequest) -> HttpResponse:
    gruppo_attivo = request.POST.get("gruppo")
    forms = []
    for gruppo in Gruppo:
        if gruppo in GRUPPI_GESTIONALE:  # dati fiscali: solo da Gestionale → Impostazioni (permessi riservati)
            continue
        if request.method == "POST" and gruppo.name == gruppo_attivo:
            form = ParametriGruppoForm(gruppo, request.POST, request.FILES)
            if form.is_valid():
                servizi.scrivi_molti({p.chiave: form.cleaned_data[p.chiave] for p in form.parametri}, request.user)
                for chiave in form.segreti_impostati:
                    if form.cleaned_data.get(f"{chiave}__cancella"):
                        servizi.cancella_segreto(chiave, request.user)
                for chiave in form.immagini:
                    if form.cleaned_data.get(f"{chiave}__cancella"):
                        servizi.cancella_valore(chiave, request.user)
                messages.success(request, f"Parametri '{gruppo.value}' salvati.")
                # Dopo il salvataggio la scheda appena salvata resta aperta (le altre chiuse).
                return redirect(f"{reverse('configurazione:pagina')}?aperto={gruppo.name}#gruppo-{gruppo.name}")
            messages.error(request, "Correggi gli errori evidenziati: nessun valore è stato salvato.")
        else:
            form = ParametriGruppoForm(gruppo)
        forms.append(form)
    return render(request, "configurazione/pagina.html", {"forms": forms, "aperto": request.GET.get("aperto", "")})


@richiede_permesso(F.CONFIGURAZIONE)
def storico(request: HttpRequest) -> HttpResponse:
    voci = (
        ValoreParametro.history.select_related("history_user")
        .exclude(chiave__startswith="gestionale.")  # storico dei dati fiscali: in Gestionale → Impostazioni
        .order_by("-history_date")[:200]
    )
    return render(request, "configurazione/storico.html", {"voci": voci})


@richiede_permesso(F.CONFIGURAZIONE)
@require_http_methods(["POST"])
def anteprima_tracciato(request: HttpRequest) -> HttpResponse:
    """Genera il file d'esempio con il tracciato scritto nel form (non ancora salvato)."""
    from metodo.tracciati import esempi, linguaggio

    campo = request.POST.get("campo", "")
    sorgente = request.POST.get(campo, "") if "tracciato" in campo else ""
    contesto: dict[str, object] = {}
    try:
        tracciato = linguaggio.compila(sorgente)
        file = linguaggio.genera(tracciato, esempi.DOCUMENTO_ESEMPIO, esempi.ORDINI_ESEMPIO)
        contesto = {"file": file, "visibile": file.testo.replace("\r\n", "⏎\n").replace("\t", "→")}
    except linguaggio.ErroreTracciato as exc:
        contesto = {"errore": str(exc)}
    return render(request, "configurazione/_anteprima_tracciato.html", contesto)


@richiede_permesso(F.CONFIGURAZIONE)
def guida_tracciati(request: HttpRequest) -> HttpResponse:
    from metodo.tracciati import esempi, linguaggio

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


@require_POST
@richiede_permesso(F.CONFIGURAZIONE)
def prova_casella(request: HttpRequest) -> HttpResponse:
    """Prova la casella ordini con i valori scritti nel form (password vuota = quella salvata). Non salva nulla."""
    from metodo.importazione import posta

    prefisso = Gruppo.CASELLA.name.lower()
    dati = {k.removeprefix(f"{prefisso}-"): v for k, v in request.POST.items() if k.startswith(f"{prefisso}-")}
    esito = posta.prova_connessione(posta.parametri_casella(modulo=dati))
    return render(request, "configurazione/_esito_prova.html", {"esito": esito})


@require_GET
def logo(request: HttpRequest) -> HttpResponse:
    """Logo dell'azienda (PNG già normalizzato al caricamento). Pubblico: compare anche nella pagina di accesso."""
    import base64
    import hashlib

    dati = servizi.leggi("azienda.logo")
    if not dati:
        raise Http404
    etag = '"' + hashlib.sha256(dati.encode()).hexdigest()[:16] + '"'
    if request.headers.get("If-None-Match") == etag:
        return HttpResponseNotModified()
    r = HttpResponse(base64.b64decode(dati), content_type="image/png")
    r["ETag"] = etag
    r["Cache-Control"] = "public, max-age=86400"
    r["X-Content-Type-Options"] = "nosniff"
    return r
