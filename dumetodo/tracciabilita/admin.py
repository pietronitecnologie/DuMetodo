from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    Controllo,
    ControlloQualita,
    Elemento,
    EsitoControllo,
    FileOrdine,
    Test,
    Traccia,
)


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
    list_display = ("codice", "creato_il")
    search_fields = ("codice",)
    readonly_fields = ("creato_da",)


@admin.register(Traccia)
class TracciaAdmin(SoloLettura):
    list_display = ("data", "elemento", "ordine", "chiave", "valore", "reparto")
    list_filter = ("chiave", "reparto")
    search_fields = ("elemento__codice", "ordine__numero", "valore")
    list_select_related = ("elemento", "ordine", "reparto")
    date_hierarchy = "data"


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


@admin.register(FileOrdine)
class FileOrdineAdmin(SoloLettura):
    list_display = ("ordine", "nome_originale", "tipo_contenuto", "dimensione", "caricato_il", "caricato_da")
    search_fields = ("ordine__numero", "nome_originale")
    exclude = ("file",)
