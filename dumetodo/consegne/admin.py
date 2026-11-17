from django.contrib import admin

from .models import DDT, Consegna, RigaConsegnata


class SoloLettura(admin.ModelAdmin):
    def has_add_permission(self, request):  # type: ignore[no-untyped-def]
        return False

    def has_change_permission(self, request, obj=None):  # type: ignore[no-untyped-def]
        return False

    def has_delete_permission(self, request, obj=None):  # type: ignore[no-untyped-def]
        return False


@admin.register(Consegna)
class ConsegnaAdmin(SoloLettura):
    list_display = ("pk", "creata_il", "creata_da", "selezione", "file_nome")
    exclude = ("file_contenuto",)


@admin.register(DDT)
class DDTAdmin(SoloLettura):
    list_display = ("numero", "anno", "data", "creato_da", "file_nome")
    search_fields = ("numero",)
    exclude = ("file_contenuto",)


@admin.register(RigaConsegnata)
class RigaConsegnataAdmin(SoloLettura):
    list_display = ("riga", "consegna", "qta", "ddt")
    list_select_related = ("riga__ordine", "consegna", "ddt")
