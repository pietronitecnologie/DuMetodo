from typing import Any

from django import template

register = template.Library()


@register.inclusion_tag("tracciabilita/_filtri.html", takes_context=True)
def filtri(context: template.Context, form: Any, nomi: str) -> dict[str, Any]:
    """Modulo di filtro (GET) con i soli campi indicati, separati da spazi."""
    return {"form": form, "campi": [form[n] for n in nomi.split()], "request": context["request"]}
