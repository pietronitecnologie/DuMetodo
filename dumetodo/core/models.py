from django.db import models
from simple_history.models import HistoricalRecords


class ModelloTemporale(models.Model):
    creato_il = models.DateTimeField("creato il", auto_now_add=True)
    aggiornato_il = models.DateTimeField("aggiornato il", auto_now=True)

    class Meta:
        abstract = True


class Reparto(ModelloTemporale):
    """Reparto produttivo (ex ``Plan.Location``).

    La soglia settimanale sostituisce le chiavi ``SOGLIA_<AREA>`` della vecchia tabella
    Configuration: è un dato del reparto, non una stringa in una tabella chiave/valore.
    """

    codice = models.CharField("codice", max_length=30, unique=True, help_text="Es. GK01-PLS (ex path code).")
    nome = models.CharField("nome", max_length=80, unique=True)
    soglia_ore_settimanali = models.DecimalField(
        "capacità settimanale (ore)", max_digits=8, decimal_places=2, null=True, blank=True
    )
    attivo = models.BooleanField("attivo", default=True)
    gruppo_report = models.CharField(
        "gruppo nel report di pianificazione",
        max_length=80,
        blank=True,
        default="",
        help_text="Reparti con lo stesso gruppo vengono sommati in un'unica riga nel report Excel/email "
        "(in Metodo: 'Area Quadristica', con 6 nomi di reparto scritti nel codice).",
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "reparto"
        verbose_name_plural = "reparti"
        ordering = ["nome"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(soglia_ore_settimanali__isnull=True) | models.Q(soglia_ore_settimanali__gte=0),
                name="reparto_soglia_non_negativa",
            )
        ]

    def __str__(self) -> str:
        return self.nome


class Funzionalita(models.Model):
    """Modello "contenitore" dei permessi per funzione (nessuna tabella: managed=False).
    I permessi sono dichiarati in ``core/permessi.py`` e sincronizzati a ogni migrate."""

    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = "funzionalità"
        verbose_name_plural = "funzionalità"

    def __str__(self) -> str:
        return "funzionalità"
