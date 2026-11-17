from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import OreLavorate


@admin.register(OreLavorate)
class OreLavorateAdmin(SimpleHistoryAdmin):
    list_display = ("settimana", "ore", "note", "aggiornato_da", "aggiornato_il")
