from django.http import HttpRequest

from .permessi import is_cliente, is_fornitore


def ruoli(request: HttpRequest) -> dict[str, object]:
    """Identità dell'utente; i permessi nei template si leggono con ``perms.core.<funzione>``."""
    return {"is_cliente": is_cliente(request.user), "is_fornitore": is_fornitore(request.user)}


def azienda(request: HttpRequest) -> dict[str, object]:
    from dumetodo.configurazione import servizi as config

    from .versione import versione

    return {
        "versione_dumetodo": versione(),
        "nome_fornitore": config.leggi("azienda.nome_fornitore") or "Fornitore",
        "nome_cliente": config.leggi("azienda.nome_cliente") or "Cliente",
    }


def navigazione(request: HttpRequest) -> dict[str, object]:
    from django.utils import timezone

    from .menu import menu_utente

    contesto: dict[str, object] = {"menu": menu_utente(request.user, request.path), "oggi": timezone.localdate()}
    if getattr(request.user, "is_authenticated", False):
        from django import forms

        from dumetodo.ordini.selezione import CampoOrdine, WidgetOrdine

        class VaiOrdine(forms.Form):
            ordine = CampoOrdine(
                required=False, widget=WidgetOrdine(attrs={"id": "dm-vai-ordine", "placeholder": "Vai all'ordine…"})
            )

        contesto["vai_ordine"] = VaiOrdine()
    return contesto
