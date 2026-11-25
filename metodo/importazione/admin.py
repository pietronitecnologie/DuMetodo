from django.contrib import admin

from .models import FileImportato, MessaggioEmail, ProblemaImportazione, StatoCasella


class SoloLettura(admin.ModelAdmin):
    def has_add_permission(self, request):  # type: ignore[no-untyped-def]
        return False

    def has_change_permission(self, request, obj=None):  # type: ignore[no-untyped-def]
        return False


@admin.register(StatoCasella)
class StatoCasellaAdmin(admin.ModelAdmin):
    list_display = ("casella", "uidvalidity", "ultimo_uid", "ultima_lettura_il")


@admin.register(MessaggioEmail)
class MessaggioEmailAdmin(SoloLettura):
    list_display = ("oggetto", "mittente", "ricevuto_il", "esito")
    list_filter = ("esito",)
    search_fields = ("oggetto", "mittente", "message_id")


@admin.register(FileImportato)
class FileImportatoAdmin(SoloLettura):
    list_display = ("nome", "origine", "stato", "creato_il", "ordini_creati", "ordini_aggiornati", "ordini_scartati")
    list_filter = ("stato", "origine")
    exclude = ("contenuto",)


@admin.register(ProblemaImportazione)
class ProblemaImportazioneAdmin(SoloLettura):
    list_display = ("messaggio", "livello", "chiave_ordine", "gestito", "creato_il")
    list_filter = ("livello", "gestito")
