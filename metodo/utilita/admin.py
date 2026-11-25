from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Annuncio


@admin.register(Annuncio)
class AnnuncioAdmin(SimpleHistoryAdmin):
    list_display = ("data", "attivo", "permanente", "attivato_il", "creato_da")
    readonly_fields = ("testo", "attivato_il", "creato_da")
