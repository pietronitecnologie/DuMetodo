from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords


class OreLavorate(models.Model):
    """Ore effettivamente lavorate in produzione in una settimana.

    In Metodo arrivavano dalle timbrature (app separata, fuori da Metodo v2): qui si inseriscono a
    mano o si importano, e servono a calcolare la produttività reale (pezzi/ora, efficienza)."""

    settimana = models.DateField("settimana (lunedì)", unique=True)
    ore = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.CharField(max_length=200, blank=True, default="")
    aggiornato_il = models.DateTimeField(auto_now=True)
    aggiornato_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    history = HistoricalRecords()

    class Meta:
        verbose_name = "ore lavorate"
        verbose_name_plural = "ore lavorate"
        ordering = ["-settimana"]
        constraints = [
            models.CheckConstraint(condition=models.Q(ore__gte=0), name="ore_lavorate_non_negative"),
            models.CheckConstraint(condition=models.Q(settimana__week_day=2), name="ore_lavorate_di_lunedi"),
        ]

    def __str__(self) -> str:
        return f"{self.settimana:%d/%m/%Y}: {self.ore} h"
