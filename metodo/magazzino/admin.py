from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    Componente,
    ComponenteDistinta,
    ConteggioInventario,
    DDTIngresso,
    DistintaBase,
    MaterialeIngresso,
    RichiestaMateriale,
)


@admin.register(Componente)
class ComponenteAdmin(SimpleHistoryAdmin):
    list_display = ("codice", "variante", "descrizione", "um", "prezzo_medio", "fornitore", "attivo")
    list_filter = ("attivo", "um")
    search_fields = ("codice", "variante", "descrizione", "fornitore")


@admin.register(ConteggioInventario)
class ConteggioInventarioAdmin(admin.ModelAdmin):
    list_display = ("componente", "qta", "data", "utente")
    list_select_related = ("componente", "utente")
    search_fields = ("componente__codice",)
    date_hierarchy = "data"
    raw_id_fields = ("componente",)


@admin.register(RichiestaMateriale)
class RichiestaMaterialeAdmin(admin.ModelAdmin):
    list_display = ("codice", "variante", "reparto", "data_richiesta", "numero_richieste", "urgente", "ricevuta")
    list_filter = ("reparto", "ricevuta", "urgente")
    search_fields = ("codice", "variante")
    readonly_fields = ("ultima_email",)


class ComponenteDistintaInline(admin.TabularInline):
    model = ComponenteDistinta
    extra = 0
    raw_id_fields = ("componente",)


@admin.register(DistintaBase)
class DistintaBaseAdmin(SimpleHistoryAdmin):
    list_display = ("articolo", "variante", "descrizione", "eliminata_il")
    list_filter = (("eliminata_il", admin.EmptyFieldListFilter),)
    search_fields = ("articolo__codice", "variante", "descrizione")
    raw_id_fields = ("articolo",)
    inlines = [ComponenteDistintaInline]


class MaterialeIngressoInline(admin.TabularInline):
    model = MaterialeIngresso
    extra = 0
    raw_id_fields = ("componente",)


@admin.register(DDTIngresso)
class DDTIngressoAdmin(admin.ModelAdmin):
    list_display = ("numero", "data", "fornitore", "origine", "creato_da")
    list_filter = ("origine",)
    search_fields = ("numero", "fornitore")
    inlines = [MaterialeIngressoInline]
