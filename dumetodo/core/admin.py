from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Reparto


@admin.register(Reparto)
class RepartoAdmin(SimpleHistoryAdmin):
    list_display = ("codice", "nome", "soglia_ore_settimanali", "gruppo_report", "attivo")
    list_filter = ("attivo",)
    search_fields = ("codice", "nome")
