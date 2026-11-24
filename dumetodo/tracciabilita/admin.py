from django.contrib import admin

from .models import Elemento, Traccia


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
