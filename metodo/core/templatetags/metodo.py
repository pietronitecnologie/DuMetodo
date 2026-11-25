from typing import Any

from django import template

register = template.Library()


@register.filter
def get_item(d: Any, chiave: str) -> Any:
    return d.get(chiave, "") if hasattr(d, "get") else ""
