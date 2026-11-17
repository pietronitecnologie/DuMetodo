from django.contrib import admin, messages

from .models import EmailInUscita, Nota, StatoEmail


@admin.register(Nota)
class NotaAdmin(admin.ModelAdmin):
    list_display = ("ordine", "autore_nome", "lato", "visibilita", "creata_il", "eliminata_il")
    list_filter = ("lato", "visibilita")
    search_fields = ("testo", "ordine__numero", "autore_nome")
    readonly_fields = [f.name for f in Nota._meta.fields]

    def has_add_permission(self, request):  # type: ignore[no-untyped-def]
        return False


@admin.register(EmailInUscita)
class EmailInUscitaAdmin(admin.ModelAdmin):
    list_display = ("oggetto", "stato", "tentativi", "creata_il", "inviata_il", "riferimento")
    list_filter = ("stato",)
    search_fields = ("oggetto", "riferimento")
    readonly_fields = [f.name for f in EmailInUscita._meta.fields]
    actions = ["rimetti_in_coda"]

    @admin.action(description="Rimetti in coda le email selezionate")
    def rimetti_in_coda(self, request, queryset):  # type: ignore[no-untyped-def]
        from django.utils import timezone

        n = queryset.exclude(stato=StatoEmail.INVIATA).update(
            stato=StatoEmail.IN_CODA, tentativi=0, prossimo_tentativo_il=timezone.now()
        )
        self.message_user(request, f"{n} email rimesse in coda.", messages.SUCCESS)
