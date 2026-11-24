"""Viste di Utenti e permessi: sottili, le regole stanno in ``utenti.py``."""

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from dumetodo.tracciatura.da_qualita import UTENTE_SISTEMA

from . import utenti as servizi
from .models import EventoUtente
from .permessi import FUNZIONI, GRUPPO_CLIENTE, PROFILI, F, Sezione, ha_permesso, richiede_permesso
from .utenti import OperazioneUtenti

AREA = (F.UTENTI_VEDERE, F.UTENTI_GESTIRE, F.UTENTI_PERMESSI, F.UTENTI_PASSWORD)


@richiede_permesso(*AREA)
def lista(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()[:50]
    stato = request.GET.get("stato", "attivi")
    tipo = request.GET.get("tipo", "")
    # L'utente tecnico «dumetodo-sistema» (autore dei dati storici importati) non è una persona: nascosto.
    utenti = (
        User.objects.exclude(username=UTENTE_SISTEMA)
        .annotate(
            n_permessi=Count("user_permissions", filter=Q(user_permissions__content_type__model="funzionalita")),
            cliente=Count("groups", filter=Q(groups__name=GRUPPO_CLIENTE)),
        )
        .select_related("profilo_dumetodo")
    )
    if q:
        utenti = utenti.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q)
        )
    if stato == "attivi":
        utenti = utenti.filter(is_active=True)
    elif stato == "disattivi":
        utenti = utenti.filter(is_active=False)
    if tipo == "cliente":
        utenti = utenti.filter(cliente__gt=0)
    elif tipo == "fornitore":
        utenti = utenti.filter(cliente=0)
    return render(
        request,
        "utenti/lista.html",
        {
            "utenti": utenti.order_by("-is_active", "username"),
            "totale_funzioni": len(FUNZIONI),
            "filtri": {"q": q, "stato": stato, "tipo": tipo},
        },
    )


def _contesto(request: HttpRequest, utente: User | None, **extra: object) -> dict[str, object]:
    attore = request.user
    attuali = servizi.permessi_utente(utente) if utente else set()
    if request.method == "POST" and request.POST.get("azione") == "permessi":
        attuali = set(request.POST.getlist("permesso"))
    concedibili = servizi.concedibili(attore)
    sezioni = [
        (s, [(f, f.codice in attuali, f.codice in concedibili) for f in FUNZIONI if f.sezione == s]) for s in Sezione
    ]
    modificabile = utente is None or servizi.puo_modificare(attore, utente)
    valori = {
        "username": getattr(utente, "username", ""),
        "nome": getattr(utente, "first_name", ""),
        "cognome": getattr(utente, "last_name", ""),
        "email": getattr(utente, "email", ""),
        "note": servizi.profilo(utente).note if utente else "",
    }
    if request.method == "POST" and request.POST.get("azione") == "dati":
        valori = {c: request.POST.get(c, "") for c in valori}
    return {
        "utente": utente,
        "valori": valori,
        "sezioni": [(s, voci) for s, voci in sezioni if voci],
        "profili": [p for p in PROFILI if servizi.codici_profilo(p) <= concedibili],
        "is_utente_cliente": bool(utente and utente.groups.filter(name=GRUPPO_CLIENTE).exists()),
        "deve_cambiare": bool(utente and servizi.profilo(utente).deve_cambiare_password),
        "eventi": EventoUtente.objects.filter(utente=utente).select_related("attore")[:50] if utente else [],
        "modificabile": modificabile,
        "puo_dati": modificabile and ha_permesso(attore, F.UTENTI_GESTIRE),
        "puo_password": modificabile and ha_permesso(attore, F.UTENTI_PASSWORD) and utente != attore,
        "puo_permessi": modificabile and ha_permesso(attore, F.UTENTI_PERMESSI),
        "puo_superuser": attore.is_superuser and utente is not None,
        **extra,
    }


@richiede_permesso(F.UTENTI_GESTIRE)
def nuovo(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        try:
            utente = servizi.salva_dati(
                request.user,
                None,
                username=request.POST.get("username", ""),
                nome=request.POST.get("nome", ""),
                cognome=request.POST.get("cognome", ""),
                email=request.POST.get("email", ""),
                attivo=True,
                cliente=request.POST.get("cliente") == "on",
                note=request.POST.get("note", ""),
                password=request.POST.get("password", ""),
            )
        except OperazioneUtenti as exc:
            messages.error(request, str(exc))
        else:
            profilo = request.POST.get("profilo", "")
            if profilo and ha_permesso(request.user, F.UTENTI_PERMESSI):
                try:
                    servizi.applica_profilo_utente(request.user, utente, profilo)
                except OperazioneUtenti as exc:
                    messages.warning(request, f"Utente creato, ma il profilo non è stato applicato: {exc}")
            messages.success(request, f"Utente {utente.username} creato: al primo accesso dovrà cambiare la password.")
            return redirect("utenti:modifica", pk=utente.pk)
    return render(request, "utenti/modifica.html", _contesto(request, None))


@richiede_permesso(*AREA)
def modifica(request: HttpRequest, pk: int) -> HttpResponse:
    utente = get_object_or_404(User.objects.exclude(username=UTENTE_SISTEMA), pk=pk)
    extra: dict[str, object] = {}
    if request.method == "POST":
        azione = request.POST.get("azione", "")
        try:
            if azione == "dati":
                servizi.salva_dati(
                    request.user,
                    utente,
                    username=request.POST.get("username", ""),
                    nome=request.POST.get("nome", ""),
                    cognome=request.POST.get("cognome", ""),
                    email=request.POST.get("email", ""),
                    attivo=request.POST.get("attivo") == "on",
                    cliente=request.POST.get("cliente") == "on",
                    note=request.POST.get("note", ""),
                )
                messages.success(request, "Dati salvati.")
            elif azione == "permessi":
                servizi.salva_permessi(request.user, utente, request.POST.getlist("permesso"))
                messages.success(request, "Permessi salvati.")
            elif azione == "profilo":
                servizi.applica_profilo_utente(request.user, utente, request.POST.get("profilo", ""))
                messages.success(request, f"Profilo {request.POST.get('profilo')} applicato.")
            elif azione == "password":
                servizi.imposta_password(
                    request.user,
                    utente,
                    request.POST.get("password", ""),
                    da_cambiare=request.POST.get("da_cambiare") == "on",
                )
                messages.success(request, "Password impostata.")
            elif azione == "password_temporanea":
                # Mostrata solo in questa risposta (non in un messaggio salvato nella sessione).
                extra["password_temporanea"] = servizi.genera_password_temporanea(request.user, utente)
            elif azione == "superuser":
                servizi.imposta_superuser(request.user, utente, request.POST.get("valore") == "1")
                messages.success(request, "Impostazione superuser salvata.")
            else:
                messages.error(request, "Azione non riconosciuta.")
            if not extra:
                return redirect("utenti:modifica", pk=utente.pk)
            utente.refresh_from_db()
        except OperazioneUtenti as exc:
            messages.error(request, str(exc))
    risposta = render(request, "utenti/modifica.html", _contesto(request, utente, **extra))
    if extra:
        risposta["Cache-Control"] = "no-store"
    return risposta


@login_required
def mia_password(request: HttpRequest) -> HttpResponse:
    """Ogni utente cambia la propria password (obbligatorio dopo una password temporanea)."""
    obbligatoria = servizi.profilo(request.user).deve_cambiare_password
    if request.method == "POST":
        try:
            servizi.cambia_propria_password(
                request.user,
                request.POST.get("attuale", ""),
                request.POST.get("nuova", ""),
                request.POST.get("conferma", ""),
            )
        except OperazioneUtenti as exc:
            messages.error(request, str(exc))
        else:
            update_session_auth_hash(request, request.user)  # resta collegato
            messages.success(request, "Password cambiata.")
            return redirect("home")
    return render(request, "utenti/mia_password.html", {"obbligatoria": obbligatoria})
