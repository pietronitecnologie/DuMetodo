"""Viste del magazzino: sottili, la logica sta in ``servizi``. Tutte le modifiche sono POST."""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.http import require_GET, require_POST

from dumetodo.anagrafica.models import Articolo, Tabulato, UnitaMisura
from dumetodo.core.models import Reparto
from dumetodo.core.permessi import F, richiede_permesso

from . import servizi
from .models import (
    Componente,
    ComponenteDistinta,
    DDTIngresso,
    DistintaBase,
    RichiestaMateriale,
    TipoEmailRichiesta,
)
from .servizi import OperazioneNonConsentita

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PER_PAGINA = 50
MAX_ESITI_SCARTI = 100


# ----------------------------------------------------------------------------------------- aiuti
def _download(nome: str, contenuto: bytes) -> HttpResponse:
    r = HttpResponse(contenuto, content_type=XLSX)
    nome = nome.replace('"', "").replace("/", "_")
    r["Content-Disposition"] = f'attachment; filename="{nome}"'
    return r


def _url_interno(request: HttpRequest, url: str | None) -> str:
    """L'URL se punta a una pagina di DuMetodo, altrimenti stringa vuota (niente redirect esterni)."""
    url = (url or "").strip()
    if url.startswith("/") and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=False
    ):
        return url
    return ""


def _torna(request: HttpRequest, predefinito: str) -> HttpResponse:
    """Torna alla pagina indicata nel POST (con i filtri), solo se è un URL interno."""
    return redirect(_url_interno(request, request.POST.get("torna")) or predefinito)


def _pagina(request: HttpRequest, qs: Any, per_pagina: int = PER_PAGINA) -> dict[str, Any]:
    """Pagina corrente + querystring dei filtri da ripetere nei link di paginazione."""
    filtri = request.GET.copy()
    filtri.pop("pagina", None)
    return {"pagina": Paginator(qs, per_pagina).get_page(request.GET.get("pagina")), "filtri_qs": filtri.urlencode()}


def _data(valore: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat((valore or "").strip())
    except ValueError:
        return None


def _anno(valore: str | None) -> int | None:
    v = (valore or "").strip()
    return int(v) if v.isdigit() and 2000 <= int(v) <= 2100 else None  # Metodo: 500 se non numerico


def _ids(request: HttpRequest, nome: str = "id") -> list[int]:
    dati = request.POST if request.method == "POST" else request.GET
    return [int(i) for i in dati.getlist(nome) if i.isdigit()][:1000]


def _esegui(request: HttpRequest, funzione: Any, *args: Any, ok: str = "", **kwargs: Any) -> Any:
    """Chiama un servizio e trasforma l'errore in un messaggio. Ritorna il risultato o None."""
    try:
        risultato = funzione(request.user, *args, **kwargs)
    except OperazioneNonConsentita as exc:
        messages.error(request, str(exc))
        return None
    if ok:
        messages.success(request, ok.format(r=risultato))
    return risultato if risultato is not None else True


# ----------------------------------------------------------------------------------------- componenti
@require_GET
@richiede_permesso(F.MAGAZZINO_ANAGRAFICHE)
def componenti(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    qs = servizi.cerca_componenti(q, solo_attivi=request.GET.get("attivi") == "1")
    return render(
        request,
        "magazzino/componenti.html",
        {"q": q, "unita": UnitaMisura.choices, "totale": qs.count(), **_pagina(request, qs)},
    )


@require_POST
@richiede_permesso(F.MAGAZZINO_ANAGRAFICHE)
def componente_nuovo(request: HttpRequest) -> HttpResponse:
    p = request.POST
    c = _esegui(
        request,
        servizi.crea_componente,
        p.get("codice", ""),
        p.get("variante", ""),
        descrizione=p.get("descrizione", ""),
        um=p.get("um", ""),
        prezzo_medio=p.get("prezzo_medio", ""),
        fornitore=p.get("fornitore", ""),
        note=p.get("note", ""),
        ok="Componente {r} creato.",
    )
    if not c:
        return redirect("magazzino:componenti")
    return redirect(f"{reverse('magazzino:componenti')}?{urlencode({'q': c.codice})}")


@richiede_permesso(F.MAGAZZINO_ANAGRAFICHE)
def componente_modifica(request: HttpRequest, pk: int) -> HttpResponse:
    componente = get_object_or_404(Componente, pk=pk)
    if request.method == "POST":
        p = request.POST
        if _esegui(
            request,
            servizi.modifica_componente,
            pk,
            descrizione=p.get("descrizione", ""),
            um=p.get("um", ""),
            prezzo_medio=p.get("prezzo_medio", ""),
            fornitore=p.get("fornitore", ""),
            note=p.get("note", ""),
            attivo=p.get("attivo") == "on",
            ok="Componente {r} aggiornato.",
        ):
            return _torna(request, reverse("magazzino:componenti"))
    storico = componente.history.select_related("history_user")[:20]
    return render(
        request,
        "magazzino/componente.html",
        {
            "componente": componente,
            "unita": UnitaMisura.choices,
            "storico": storico,
            "torna": _url_interno(request, request.GET.get("torna") or request.POST.get("torna"))
            or reverse("magazzino:componenti"),
        },
    )


@require_GET
@richiede_permesso(
    F.MAGAZZINO_ANAGRAFICHE,
    F.MAGAZZINO_INVENTARIO,
    F.MAGAZZINO_RICHIESTE,
    F.MAGAZZINO_DISTINTE_MODIFICA,
    F.MAGAZZINO_DDT,
)
def suggerimenti_componenti(request: HttpRequest) -> HttpResponse:
    """Opzioni per il campo codice (HTMX). Metodo inviava l'intera anagrafica in ogni pagina."""
    q = request.GET.get("codice", "").strip()
    risultati = servizi.cerca_componenti(q, solo_attivi=True)[:20] if len(q) >= 2 else []
    return render(request, "magazzino/_suggerimenti.html", {"componenti": risultati})


# ----------------------------------------------------------------------------------------- articoli finiti
@require_GET
@richiede_permesso(F.MAGAZZINO_ANAGRAFICHE)
def articoli(request: HttpRequest) -> HttpResponse:
    g = request.GET
    qs = Articolo.objects.select_related("reparto").order_by("codice")
    if g.get("q"):
        qs = qs.filter(Q(codice__icontains=g["q"].strip()) | Q(descrizione__icontains=g["q"].strip()))
    if g.get("codice"):
        qs = qs.filter(codice__icontains=g["codice"].strip())
    if g.get("descrizione"):
        qs = qs.filter(descrizione__icontains=g["descrizione"].strip())
    if g.get("reparto", "").isdigit():
        qs = qs.filter(reparto_id=int(g["reparto"]))
    elif g.get("reparto") == "nessuno":
        qs = qs.filter(reparto__isnull=True)
    if g.get("um") in UnitaMisura.values:
        qs = qs.filter(um=g["um"])
    if g.get("tabulato", "").isdigit():
        qs = qs.filter(tabulato=int(g["tabulato"]))
    if g.get("nuovo") in ("0", "1"):
        qs = qs.filter(nuovo=g["nuovo"] == "1")
    return render(
        request,
        "magazzino/articoli.html",
        {
            "f": g,
            "reparti": Reparto.objects.order_by("nome"),
            "unita": UnitaMisura.choices,
            "tabulati": Tabulato.choices,
            "totale": qs.count(),
            **_pagina(request, qs),
        },
    )


@require_POST
@richiede_permesso(F.MAGAZZINO_ANAGRAFICHE)
def articolo_modifica(request: HttpRequest, pk: int) -> HttpResponse:
    p = request.POST
    tabulato = p.get("tabulato", "0")
    _esegui(
        request,
        servizi.modifica_articolo,
        pk,
        descrizione=p.get("descrizione", ""),
        reparto_id=int(p["reparto"]) if p.get("reparto", "").isdigit() else None,
        um=p.get("um", ""),
        tabulato=int(tabulato) if tabulato.isdigit() else -1,
        ok="Articolo {r} aggiornato.",
    )
    return _torna(request, reverse("magazzino:articoli"))


@require_POST
@richiede_permesso(F.MAGAZZINO_ANAGRAFICHE)
def articolo_crea(request: HttpRequest) -> HttpResponse:
    p = request.POST
    tabulato = p.get("tabulato", "0")
    _esegui(
        request,
        servizi.crea_articolo,
        codice=p.get("codice", ""),
        descrizione=p.get("descrizione", ""),
        reparto_id=int(p["reparto"]) if p.get("reparto", "").isdigit() else None,
        um=p.get("um", ""),
        tabulato=int(tabulato) if tabulato.isdigit() else -1,
        prezzo=p.get("prezzo", ""),
        ok="Articolo {r} creato.",
    )
    return _torna(request, reverse("magazzino:articoli"))


@require_POST
@richiede_permesso(F.MAGAZZINO_ANAGRAFICHE)
def articolo_nuovo(request: HttpRequest, pk: int) -> HttpResponse:
    nuovo = request.POST.get("nuovo") == "1"
    _esegui(
        request,
        servizi.imposta_nuovo,
        pk,
        nuovo,
        ok="Articolo {r} impostato come " + ("nuovo." if nuovo else "non nuovo."),
    )
    return _torna(request, reverse("magazzino:articoli"))


# ----------------------------------------------------------------------------------------- inventario
@require_GET
@richiede_permesso(F.MAGAZZINO_INVENTARIO)
def inventario(request: HttpRequest) -> HttpResponse:
    vista = "storico" if request.GET.get("vista") == "storico" else "ultimi"
    q, anno = request.GET.get("q", "").strip(), _anno(request.GET.get("anno"))
    qs = servizi.storico_conteggi(q, anno) if vista == "storico" else servizi.ultimi_conteggi(q)
    return render(
        request,
        "magazzino/inventario.html",
        {"vista": vista, "q": q, "anno": anno, "anni": servizi.anni_inventario(), **_pagina(request, qs)},
    )


@require_POST
@richiede_permesso(F.MAGAZZINO_INVENTARIO)
def inventario_aggiungi(request: HttpRequest) -> HttpResponse:
    p = request.POST
    _esegui(
        request,
        servizi.registra_conteggio,
        p.get("codice", ""),
        p.get("variante", ""),
        p.get("qta", ""),
        ok="Conteggio registrato: {r}.",
    )
    return _torna(request, reverse("magazzino:inventario"))


@require_GET
@richiede_permesso(F.MAGAZZINO_INVENTARIO)
def inventario_esporta(request: HttpRequest) -> HttpResponse:
    tipo = request.GET.get("tipo", "tutti")
    nome, contenuto = servizi.xlsx_inventario(tipo, _anno(request.GET.get("anno")))
    return _download(nome, contenuto)


# ----------------------------------------------------------------------------------------- richieste materiali
@require_GET
@richiede_permesso(F.MAGAZZINO_RICHIESTE, F.MAGAZZINO_RICHIESTE_GESTIONE)
def richieste(request: HttpRequest) -> HttpResponse:
    reparti = servizi.reparti_richieste()
    aperte = {
        r["reparto"]: r["n"]
        for r in RichiestaMateriale.objects.filter(ricevuta=False).values("reparto").annotate(n=Count("pk"))
    }
    return render(
        request,
        "magazzino/richieste_reparti.html",
        {"reparti": [(r, aperte.get(r.pk, 0)) for r in reparti], "tutti": Reparto.objects.filter(attivo=True)},
    )


def _reparto(codice: str) -> Reparto:
    return get_object_or_404(Reparto, codice=codice)


@require_GET
@richiede_permesso(F.MAGAZZINO_RICHIESTE, F.MAGAZZINO_RICHIESTE_GESTIONE)
def richieste_reparto(request: HttpRequest, codice: str) -> HttpResponse:
    reparto = _reparto(codice)
    return render(
        request,
        "magazzino/richieste.html",
        {"reparto": reparto, "richieste": servizi.richieste_aperte(reparto), "tipi": TipoEmailRichiesta.choices},
    )


@require_POST
@richiede_permesso(F.MAGAZZINO_RICHIESTE)
def richiesta_aggiungi(request: HttpRequest, codice: str) -> HttpResponse:
    reparto = _reparto(codice)
    p = request.POST
    esito = _esegui(
        request,
        servizi.richiedi_materiale,
        reparto,
        p.get("codice", ""),
        p.get("variante", ""),
        p.get("urgente") == "on",
    )
    if esito:
        r, creata = esito
        if creata:
            messages.success(request, f"Richiesta di {r.codice_completo} registrata.")
        else:
            messages.info(request, f"{r.codice_completo} era già richiesto: richieste totali {r.numero_richieste}.")
    return redirect("magazzino:richieste_reparto", codice=reparto.codice)


@require_GET
@richiede_permesso(F.MAGAZZINO_RICHIESTE, F.MAGAZZINO_RICHIESTE_GESTIONE)
def richieste_esporta(request: HttpRequest, codice: str) -> HttpResponse:
    return _download(*servizi.xlsx_richieste(_reparto(codice)))


@require_GET
@richiede_permesso(F.MAGAZZINO_RICHIESTE_GESTIONE)
def richieste_anteprima(request: HttpRequest, codice: str) -> HttpResponse:
    reparto = _reparto(codice)
    tutte = request.GET.get("tutte") == "1"
    try:
        messaggio = servizi.componi_email(reparto, request.GET.get("tipo", ""), _ids(request), tutte)
    except OperazioneNonConsentita as exc:
        messages.error(request, str(exc))
        return redirect("magazzino:richieste_reparto", codice=reparto.codice)
    return render(
        request, "magazzino/richieste_anteprima.html", {"reparto": reparto, "messaggio": messaggio, "tutte": tutte}
    )


@require_POST
@richiede_permesso(F.MAGAZZINO_RICHIESTE_GESTIONE)
def richieste_invia(request: HttpRequest, codice: str) -> HttpResponse:
    reparto = _reparto(codice)
    m = _esegui(
        request,
        servizi.invia_email_richieste,
        reparto,
        request.POST.get("tipo", ""),
        _ids(request),
        request.POST.get("tutte") == "1",
    )
    if m:
        messages.success(
            request,
            f"Email '{m.oggetto}' messa in invio per {len(m.richieste)} richieste ({', '.join(m.destinatari)}).",
        )
    return redirect("magazzino:richieste_reparto", codice=reparto.codice)


@require_POST
@richiede_permesso(F.MAGAZZINO_RICHIESTE_GESTIONE)
def richiesta_ricevi(request: HttpRequest, pk: int) -> HttpResponse:
    _esegui(request, servizi.ricevi_richiesta, pk, ok="Materiale {r.codice_completo} ricevuto.")
    return _torna(request, reverse("magazzino:richieste"))


@require_POST
@richiede_permesso(F.MAGAZZINO_RICHIESTE_GESTIONE)
def richiesta_elimina(request: HttpRequest, pk: int) -> HttpResponse:
    _esegui(request, servizi.elimina_richiesta, pk, ok="Richiesta eliminata.")
    return _torna(request, reverse("magazzino:richieste"))


@require_POST
@richiede_permesso(F.MAGAZZINO_RICHIESTE_GESTIONE)
def richiesta_urgenza(request: HttpRequest, pk: int) -> HttpResponse:
    _esegui(request, servizi.imposta_urgenza, pk, request.POST.get("urgente") == "1")
    return _torna(request, reverse("magazzino:richieste"))


# ----------------------------------------------------------------------------------------- distinte base
@require_GET
@richiede_permesso(F.MAGAZZINO_DISTINTE, F.MAGAZZINO_DISTINTE_MODIFICA)
def distinte(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    qs = servizi.distinte_attive(q)
    return render(request, "magazzino/distinte.html", {"q": q, **_pagina(request, qs, 30)})


@require_POST
@richiede_permesso(F.MAGAZZINO_DISTINTE_MODIFICA)
def distinta_crea(request: HttpRequest) -> HttpResponse:
    p = request.POST
    d = _esegui(
        request,
        servizi.crea_distinta,
        p.get("articolo", ""),
        p.get("variante", ""),
        p.get("descrizione", ""),
        p.get("note", ""),
        ok="{r} creata.",
    )
    return redirect(d) if d else redirect("magazzino:distinte")


@require_GET
@richiede_permesso(F.MAGAZZINO_DISTINTE, F.MAGAZZINO_DISTINTE_MODIFICA)
def distinte_esporta(request: HttpRequest) -> HttpResponse:
    return _download(*servizi.xlsx_distinte())


@require_GET
@richiede_permesso(F.MAGAZZINO_DISTINTE, F.MAGAZZINO_DISTINTE_MODIFICA)
def distinta(request: HttpRequest, pk: int) -> HttpResponse:
    d = get_object_or_404(DistintaBase.objects.attive().select_related("articolo", "creata_da", "modificata_da"), pk=pk)
    return render(
        request,
        "magazzino/distinta.html",
        {
            "distinta": d,
            "componenti": d.componenti.select_related("componente").order_by("posizione", "id"),
            "unita": UnitaMisura.choices,
        },
    )


@require_POST
@richiede_permesso(F.MAGAZZINO_DISTINTE_MODIFICA)
def distinta_modifica(request: HttpRequest, pk: int) -> HttpResponse:
    p = request.POST
    _esegui(
        request,
        servizi.modifica_distinta,
        pk,
        p.get("articolo", ""),
        p.get("variante", ""),
        p.get("descrizione", ""),
        p.get("note", ""),
        ok="{r} aggiornata.",
    )
    return redirect("magazzino:distinta", pk=pk)


@require_POST
@richiede_permesso(F.MAGAZZINO_DISTINTE_MODIFICA)
def distinta_elimina(request: HttpRequest, pk: int) -> HttpResponse:
    if _esegui(request, servizi.elimina_distinta, pk, ok="{r} eliminata."):
        return redirect("magazzino:distinte")
    return redirect("magazzino:distinta", pk=pk)


@require_POST
@richiede_permesso(F.MAGAZZINO_DISTINTE_MODIFICA)
def distinta_componente_aggiungi(request: HttpRequest, pk: int) -> HttpResponse:
    p = request.POST
    _esegui(
        request,
        servizi.aggiungi_componente_distinta,
        pk,
        p.get("codice", ""),
        p.get("variante", ""),
        p.get("quantita", ""),
        p.get("um", ""),
        p.get("note", ""),
        ok="Componente {r.componente} aggiunto.",
    )
    return redirect("magazzino:distinta", pk=pk)


def _distinta_di(pk: int) -> int:
    return get_object_or_404(ComponenteDistinta, pk=pk).distinta_id


@require_POST
@richiede_permesso(F.MAGAZZINO_DISTINTE_MODIFICA)
def componente_distinta_modifica(request: HttpRequest, pk: int) -> HttpResponse:
    distinta_id = _distinta_di(pk)
    p = request.POST
    _esegui(
        request,
        servizi.modifica_componente_distinta,
        pk,
        p.get("quantita", ""),
        p.get("um", ""),
        p.get("note", ""),
        ok="Componente {r.componente} aggiornato.",
    )
    return redirect("magazzino:distinta", pk=distinta_id)


@require_POST
@richiede_permesso(F.MAGAZZINO_DISTINTE_MODIFICA)
def componente_distinta_elimina(request: HttpRequest, pk: int) -> HttpResponse:
    distinta_id = _distinta_di(pk)
    _esegui(request, servizi.elimina_componente_distinta, pk, ok="Componente eliminato dalla distinta.")
    return redirect("magazzino:distinta", pk=distinta_id)


@require_POST
@richiede_permesso(F.MAGAZZINO_DISTINTE_MODIFICA)
def componente_distinta_sposta(request: HttpRequest, pk: int) -> HttpResponse:
    distinta_id = _distinta_di(pk)
    _esegui(request, servizi.sposta_componente_distinta, pk, request.POST.get("direzione") == "su")
    return redirect("magazzino:distinta", pk=distinta_id)


# ----------------------------------------------------------------------------------------- DDT in ingresso
@require_GET
@richiede_permesso(F.MAGAZZINO_DDT)
def ddt_lista(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    qs = DDTIngresso.objects.select_related("creato_da").annotate(
        n_materiali=Count("materiali"), da_verificare=Count("materiali", filter=Q(materiali__verificato=False))
    )
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(fornitore__icontains=q))
    return render(
        request,
        "magazzino/ddt_lista.html",
        {"q": q, "oggi": timezone.localdate(), **_pagina(request, qs.order_by("-data", "-id"), 30)},
    )


@require_POST
@richiede_permesso(F.MAGAZZINO_DDT)
def ddt_crea(request: HttpRequest) -> HttpResponse:
    p = request.POST
    d = _esegui(
        request, servizi.crea_ddt, p.get("numero", ""), _data(p.get("data")), p.get("fornitore", ""), ok="{r} creato."
    )
    return redirect(d) if d else redirect("magazzino:ddt_lista")


@require_GET
@richiede_permesso(F.MAGAZZINO_DDT)
def ddt(request: HttpRequest, pk: int) -> HttpResponse:
    d = get_object_or_404(DDTIngresso.objects.select_related("creato_da"), pk=pk)
    return render(
        request,
        "magazzino/ddt.html",
        {
            "ddt": d,
            "materiali": d.materiali.select_related("componente").order_by("riga_documento", "id"),
            "richieste": servizi.richieste_aperte_del_ddt(d),
            "richieste_chiuse": RichiestaMateriale.objects.filter(ricevuta_con__ddt=d).select_related("reparto"),
            "oggi": timezone.localdate(),
        },
    )


@require_POST
@richiede_permesso(F.MAGAZZINO_DDT)
def ddt_materiale_aggiungi(request: HttpRequest, pk: int) -> HttpResponse:
    p = request.POST
    _esegui(
        request,
        servizi.aggiungi_materiale,
        pk,
        codice=p.get("codice", ""),
        variante=p.get("variante", ""),
        descrizione=p.get("descrizione", ""),
        qta=p.get("quantita", ""),
        unita_misura=p.get("unita_misura", ""),
        prezzo_unitario=p.get("prezzo_unitario", ""),
        data_ingresso=_data(p.get("data_ingresso")),
        note=p.get("note", ""),
        ok="Materiale inserito: {r.componente} - Qtà {r.quantita}.",
    )
    return redirect("magazzino:ddt", pk=pk)


@require_POST
@richiede_permesso(F.MAGAZZINO_DDT)
def ddt_verifica(request: HttpRequest, pk: int) -> HttpResponse:
    ids = None if request.POST.get("tutto") == "1" else _ids(request)
    n = _esegui(request, servizi.imposta_verificato, pk, ids, request.POST.get("verificato", "1") == "1")
    if n is not None and n is not True:
        messages.success(request, f"Righe aggiornate: {n}.")
    return redirect("magazzino:ddt", pk=pk)


@require_POST
@richiede_permesso(F.MAGAZZINO_DDT_ELIMINA)
def ddt_materiale_elimina(request: HttpRequest, pk: int) -> HttpResponse:
    d = _esegui(request, servizi.elimina_materiale, pk, ok="Riga eliminata.")
    return redirect(d) if d else redirect("magazzino:ddt_lista")


@richiede_permesso(F.MAGAZZINO_DDT)
def ddt_importa(request: HttpRequest) -> HttpResponse:
    esito, errore = None, ""
    if request.method == "POST":
        f = request.FILES.get("file")
        if f is None:
            errore = "Seleziona un file .csv, .txt o .xml."
        elif f.size > servizi.dimensione_massima_file():
            errore = f"{f.name}: file troppo grande."
        else:
            try:
                esito = servizi.importa_file_ddt(request.user, f.name, f.read())
            except OperazioneNonConsentita as exc:
                errore = str(exc)
    return render(
        request,
        "magazzino/ddt_importa.html",
        {
            "esito": esito,
            "errore": errore,
            "scarti": esito.scarti[:MAX_ESITI_SCARTI] if esito else [],
            "altri_scarti": max(0, len(esito.scarti) - MAX_ESITI_SCARTI) if esito else 0,
            "max_mb": servizi.dimensione_massima_file() // (1024 * 1024),
        },
    )


@require_GET
@richiede_permesso(F.MAGAZZINO_DDT)
def materiali(request: HttpRequest) -> HttpResponse:
    g = request.GET
    qs = servizi.cerca_materiali(
        codice=g.get("codice", ""),
        fornitore=g.get("fornitore", ""),
        data_da=_data(g.get("data_da")),
        data_a=_data(g.get("data_a")),
        solo_da_verificare=g.get("da_verificare") == "1",
    )
    if g.get("esporta") == "1":
        return _download(*servizi.xlsx_materiali(qs))
    return render(request, "magazzino/materiali.html", {"f": g, **_pagina(request, qs)})
