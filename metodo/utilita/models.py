import datetime as dt

from django.conf import settings
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

DURATA_ANNUNCIO = dt.timedelta(days=1)


class AnnuncioQuerySet(models.QuerySet["Annuncio"]):
    def visibili(self, adesso: dt.datetime | None = None) -> "AnnuncioQuerySet":
        """Attivi e non scaduti: un annuncio non permanente dura un giorno dall'attivazione.
        In Metodo serviva un job ogni 10 minuti per disattivarli; qui la scadenza è calcolata."""
        adesso = adesso or timezone.now()
        return self.filter(attivo=True).filter(
            models.Q(permanente=True) | models.Q(attivato_il__gt=adesso - DURATA_ANNUNCIO)
        )


class Annuncio(models.Model):
    data = models.DateField("data", default=timezone.localdate)
    testo = models.TextField("testo", help_text="HTML ripulito: grassetto, corsivo, sottolineato, elenchi, link.")
    attivo = models.BooleanField(default=True)
    permanente = models.BooleanField(default=False, help_text="Se no, resta visibile un giorno dall'attivazione.")
    attivato_il = models.DateTimeField(null=True, blank=True, editable=False)
    creato_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    creato_il = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()
    objects = AnnuncioQuerySet.as_manager()

    class Meta:
        verbose_name = "annuncio"
        verbose_name_plural = "annunci"
        ordering = ["-data", "-id"]

    def __str__(self) -> str:
        return f"Annuncio del {self.data:%d/%m/%Y}"

    @property
    def scaduto(self) -> bool:
        return (
            self.attivo
            and not self.permanente
            and bool(self.attivato_il)
            and timezone.now() >= self.attivato_il + DURATA_ANNUNCIO
        )
