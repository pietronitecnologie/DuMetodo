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


class SegretoParametro(models.Model):
    """Parametro segreto (password), cifrato. Volutamente SENZA storico: le vecchie password non restano."""

    chiave = models.CharField("chiave", max_length=100, unique=True)
    cifrato = models.TextField("valore cifrato")
    aggiornato_il = models.DateTimeField("aggiornato il", auto_now=True)
    aggiornato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "parametro segreto"
        verbose_name_plural = "parametri segreti"

    def __str__(self) -> str:
        return self.chiave


class ValoreCommittente(models.Model):
    """Valore di un parametro per un cliente committente (Metodo v3): prevale su quello generale."""

    committente = models.ForeignKey("committenti.Committente", on_delete=models.CASCADE, related_name="parametri")
    chiave = models.CharField("chiave", max_length=100)
    valore = models.JSONField("valore", null=True)
    aggiornato_il = models.DateTimeField("aggiornato il", auto_now=True)
    aggiornato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "parametro del committente"
        verbose_name_plural = "parametri dei committenti"
        constraints = [models.UniqueConstraint(fields=["committente", "chiave"], name="valore_committente_unico")]

    def __str__(self) -> str:
        return f"{self.committente_id}: {self.chiave}"


class SegretoCommittente(models.Model):
    """Password di un committente (es. la sua casella ordini), cifrata e senza storico."""

    committente = models.ForeignKey("committenti.Committente", on_delete=models.CASCADE, related_name="segreti")
    chiave = models.CharField("chiave", max_length=100)
    cifrato = models.TextField("valore cifrato")
    aggiornato_il = models.DateTimeField("aggiornato il", auto_now=True)
    aggiornato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "segreto del committente"
        verbose_name_plural = "segreti dei committenti"
        constraints = [models.UniqueConstraint(fields=["committente", "chiave"], name="segreto_committente_unico")]

    def __str__(self) -> str:
        return f"{self.committente_id}: {self.chiave}"
