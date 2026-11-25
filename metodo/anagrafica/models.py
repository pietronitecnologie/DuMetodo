"""Anagrafica degli articoli finiti (ex ``Prod.Anagrafica``).

In Metodo il codice non era unico (i doppioni venivano cancellati ogni 10 ore da un job, in
ordine arbitrario, rischiando di eliminare proprio il record corretto) e non c'era alcun legame
con le righe d'ordine. Ora il codice è UNICO e gli articoli nuovi vengono registrati subito
all'importazione degli ordini.
"""

from django.db import models
from simple_history.models import HistoricalRecords

from metodo.core.models import ModelloTemporale


class UnitaMisura(models.TextChoices):
    PEZZI = "EA", "Pezzi"
    METRI = "M", "Metri"


class Tabulato(models.IntegerChoices):
    NESSUNO = 0, "Nessuno"
    T1 = 1, "Tabulato 1"
    T2 = 2, "Tabulato 2"
    T3 = 3, "Tabulato 3"
    T4 = 4, "Tabulato 4"
    T5 = 5, "Tabulato 5"


class Articolo(ModelloTemporale):
    codice = models.CharField("codice articolo", max_length=50, unique=True)
    descrizione = models.TextField("descrizione", blank=True, default="")
    reparto = models.ForeignKey(
        "core.Reparto",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="articoli",
        verbose_name="reparto di competenza",
    )
    um = models.CharField("unità di misura", max_length=3, choices=UnitaMisura.choices, default=UnitaMisura.PEZZI)
    prezzo_riferimento = models.DecimalField(
        "prezzo di riferimento", max_digits=14, decimal_places=4, null=True, blank=True
    )
    prezzo_ricalcolato = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    data_prezzo = models.DateField(null=True, blank=True)
    tabulato = models.PositiveSmallIntegerField(choices=Tabulato.choices, default=Tabulato.NESSUNO)
    nuovo = models.BooleanField("codice nuovo (da verificare)", default=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "articolo"
        verbose_name_plural = "articoli"
        ordering = ["codice"]

    def __str__(self) -> str:
        return self.codice
