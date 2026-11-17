from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    Controllo,
    ControlloQualita,
    Elemento,
    EsitoControllo,
    FileOrdine,
    Postazione,
    Reclamo,
    Test,
    TestDisplay,
    Traccia,
    WorkOrderBatteria,
)


@admin.register(Postazione)
class PostazioneAdmin(SimpleHistoryAdmin):
    list_display = ("codice", "nome", "reparto", "attiva", "ha_token", "token_generato_il")
    list_filter = ("attiva", "reparto")
    search_fields = ("codice", "nome")
    # Il token si genera dalla pagina Postazioni (viene mostrato una sola volta); qui non si vede.
    readonly_fields = ("token_generato_il",)

    @admin.display(boolean=True, description="token")
    def ha_token(self, obj: Postazione) -> bool:
        return obj.ha_token


@admin.register(Controllo)
class ControlloAdmin(SimpleHistoryAdmin):
    list_display = ("codice", "nome", "tipo", "presente_predefinito")
    list_filter = ("tipo",)
    search_fields = ("codice", "nome")
    prepopulated_fields = {"codice": ("nome",)}


@admin.register(Test)
class TestAdmin(SimpleHistoryAdmin):
    list_display = ("codice", "nome", "tipo", "attivo")
    list_filter = ("attivo", "tipo")
    search_fields = ("codice", "nome")
    filter_horizontal = ("controlli",)
    prepopulated_fields = {"codice": ("nome",)}


class SoloLettura(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None):  # type: ignore[no-untyped-def]
        return False

    def has_change_permission(self, request, obj=None):  # type: ignore[no-untyped-def]
        return False


@admin.register(Elemento)
class ElementoAdmin(admin.ModelAdmin):
    list_display = ("codice", "tipo", "lotto", "creato_il")
    list_filter = ("tipo",)
    search_fields = ("codice", "lotto")
    readonly_fields = ("creato_da",)


@admin.register(Traccia)
class TracciaAdmin(SoloLettura):
    list_display = ("data", "elemento", "ordine", "chiave", "valore", "reparto", "postazione")
    list_filter = ("chiave", "reparto", "postazione")
    search_fields = ("elemento__codice", "ordine__numero", "valore")
    list_select_related = ("elemento", "ordine", "reparto", "postazione")
    date_hierarchy = "data"


@admin.register(WorkOrderBatteria)
class WorkOrderBatteriaAdmin(SoloLettura):
    list_display = ("elemento", "stato", "aperto_il", "chiuso_il", "chiuso_da")
    list_filter = ("stato",)


class EsitoInline(admin.TabularInline):
    model = EsitoControllo
    extra = 0
    can_delete = False
    readonly_fields = ("controllo", "campione", "ko", "presente")

    def has_add_permission(self, request, obj=None):  # type: ignore[no-untyped-def]
        return False


@admin.register(ControlloQualita)
class ControlloQualitaAdmin(SoloLettura):
    list_display = ("data", "test", "ordine", "riferimento", "articolo", "eseguito_da")
    list_filter = ("test",)
    search_fields = ("articolo", "riferimento", "ordine__numero")
    inlines = [EsitoInline]


@admin.register(Reclamo)
class ReclamoAdmin(SoloLettura):
    list_display = ("codice", "data", "ordine", "articolo", "reparto")
    search_fields = ("codice", "articolo", "ordine__numero")
    exclude = ("file",)


@admin.register(TestDisplay)
class TestDisplayAdmin(SoloLettura):
    list_display = ("data", "prodotto", "sito", "articolo", "esito", "utente")
    list_filter = ("prodotto", "esito")
    search_fields = ("sito", "articolo", "lotto")


@admin.register(FileOrdine)
class FileOrdineAdmin(SoloLettura):
    list_display = ("ordine", "nome_originale", "tipo_contenuto", "dimensione", "caricato_il", "caricato_da")
    search_fields = ("ordine__numero", "nome_originale")
    exclude = ("file",)
