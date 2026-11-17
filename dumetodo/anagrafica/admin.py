from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Articolo


@admin.register(Articolo)
class ArticoloAdmin(SimpleHistoryAdmin):
    list_display = ("codice", "descrizione", "reparto", "um", "tabulato", "prezzo_riferimento", "nuovo")
    list_filter = ("reparto", "um", "tabulato", "nuovo")
    search_fields = ("codice", "descrizione")
