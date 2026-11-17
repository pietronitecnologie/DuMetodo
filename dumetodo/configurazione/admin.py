from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import ValoreParametro


@admin.register(ValoreParametro)
class ValoreParametroAdmin(SimpleHistoryAdmin):
    """Solo consultazione: le modifiche passano dalla pagina Configurazione, che valida i tipi."""

    list_display = ("chiave", "valore", "aggiornato_il", "aggiornato_da")
    readonly_fields = ("chiave", "valore", "aggiornato_il", "aggiornato_da")

    def has_add_permission(self, request):  # type: ignore[no-untyped-def]
        return False

    def has_change_permission(self, request, obj=None):  # type: ignore[no-untyped-def]
        return False
