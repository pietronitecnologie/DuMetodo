from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Ordine, RigaOrdine, StoricoScadenza


class RigaInline(admin.TabularInline):
    model = RigaOrdine
    fk_name = "ordine"
    extra = 0
    can_delete = False
    fields = (
        "n_riga",
        "articolo",
        "variante",
        "qta",
        "prezzo_unitario",
        "data_scadenza",
        "stato",
        "bloccata",
        "in_evasione",
        "bolla_emessa",
        "scorporata",
    )
    readonly_fields = fields


@admin.register(Ordine)
class OrdineAdmin(SimpleHistoryAdmin):
    list_display = ("codice", "stato", "data_scadenza", "data_consegna", "cliente")
    list_filter = ("stato", "anno", "serie")
    search_fields = ("numero", "cliente")
    inlines = [RigaInline]

    def has_delete_permission(self, request, obj=None):  # type: ignore[no-untyped-def]
        return False  # gli ordini non si cancellano: si annullano


@admin.register(StoricoScadenza)
class StoricoScadenzaAdmin(admin.ModelAdmin):
    list_display = ("ordine", "riga", "precedente", "nuova", "origine", "utente", "creato_il")
    list_filter = ("origine",)
    readonly_fields = [f.name for f in StoricoScadenza._meta.fields]
