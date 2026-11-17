from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords


class ValoreParametro(models.Model):
    """Valore impostato per un parametro del registro. Se assente vale il default del registro."""

    chiave = models.CharField("chiave", max_length=100, unique=True)
    valore = models.JSONField("valore", null=True)
    aggiornato_il = models.DateTimeField("aggiornato il", auto_now=True)
    aggiornato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "valore parametro"
        verbose_name_plural = "valori parametri"

    def __str__(self) -> str:
        return self.chiave
