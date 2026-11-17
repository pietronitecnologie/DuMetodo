from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .permessi import FUNZIONI, GRUPPO_CLIENTE, PROFILI, F, Sezione, richiede_permesso
from .utenti import OperazioneUtenti, permessi_utente, salva_utente


@richiede_permesso(F.UTENTI)
def lista(request: HttpRequest) -> HttpResponse:
    utenti = User.objects.annotate(
        n_permessi=Count("user_permissions", filter=Q(user_permissions__content_type__model="funzionalita")),
        cliente=Count("groups", filter=Q(groups__name=GRUPPO_CLIENTE)),
    ).order_by("-is_active", "username")
    return render(request, "utenti/lista.html", {"utenti": utenti, "totale_funzioni": len(FUNZIONI)})


@richiede_permesso(F.UTENTI)
def modifica(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    utente = get_object_or_404(User, pk=pk) if pk else None
    if request.method == "POST":
        try:
            utente = salva_utente(
                request.user,
                utente,
                username=request.POST.get("username", ""),
                nome=request.POST.get("nome", ""),
                cognome=request.POST.get("cognome", ""),
                email=request.POST.get("email", ""),
                attivo=request.POST.get("attivo") == "on",
                cliente=request.POST.get("cliente") == "on",
                permessi=request.POST.getlist("permesso"),
                password=request.POST.get("password", ""),
                profilo=request.POST.get("profilo", "") if request.POST.get("azione") == "profilo" else "",
            )
            messages.success(request, f"Utente {utente.username} salvato.")
            return redirect("utenti:modifica", pk=utente.pk)
        except OperazioneUtenti as exc:
            messages.error(request, str(exc))
    attuali = permessi_utente(utente) if utente else set()
    if request.method == "POST":
        attuali = set(request.POST.getlist("permesso"))
    sezioni = [(s, [(f, f.codice in attuali) for f in FUNZIONI if f.sezione == s]) for s in Sezione]
    return render(
        request,
        "utenti/modifica.html",
        {
            "utente": utente,
            "sezioni": [(s, voci) for s, voci in sezioni if voci],
            "profili": list(PROFILI),
            "is_utente_cliente": bool(utente and utente.groups.filter(name=GRUPPO_CLIENTE).exists()),
            "valori": {c: request.POST.get(c, "") for c in ("username", "nome", "cognome", "email")}
            if request.method == "POST"
            else {
                "username": getattr(utente, "username", ""),
                "nome": getattr(utente, "first_name", ""),
                "cognome": getattr(utente, "last_name", ""),
                "email": getattr(utente, "email", ""),
            },
        },
    )
