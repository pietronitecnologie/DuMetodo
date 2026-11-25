"""Stato del prelievo a blocchi da Metodo: permette di interromperlo e riprenderlo in sicurezza."""

from django.db import models


class Avanzamento(models.Model):
    """A che punto è un passo del prelievo: l'ultimo id di Metodo già salvato in Metodo v2."""

    passo = models.CharField(max_length=40, unique=True)
    ultimo_id = models.BigIntegerField(default=0)
    elaborati = models.BigIntegerField(default=0)
    completato_il = models.DateTimeField(null=True, blank=True)
    aggiornato_il = models.DateTimeField(auto_now=True)
    dati = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "avanzamento del prelievo da Metodo"
        verbose_name_plural = "avanzamento del prelievo da Metodo"

    def __str__(self) -> str:
        return f"{self.passo}: id {self.ultimo_id} ({self.elaborati})"


class Corrispondenza(models.Model):
    """Record di Metodo -> oggetto Metodo v2: evita doppioni se un blocco viene riletto."""

    tabella = models.CharField(max_length=40)
    id_metodo = models.BigIntegerField()
    id_locale = models.BigIntegerField()
    creato = models.BooleanField(default=True, help_text="Falso se l'oggetto esisteva già in Metodo v2")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tabella", "id_metodo"], name="corrispondenza_unica")]
        indexes = [models.Index(fields=["tabella", "id_locale"], name="corrispondenza_locale_idx")]

    def __str__(self) -> str:
        return f"{self.tabella} {self.id_metodo} -> {self.id_locale}"


class CodificaMetodo(models.Model):
    """Riepilogo per codice tracciato (Plan_coding) costruito leggendo le tracce di Metodo."""

    id_metodo = models.BigIntegerField(unique=True)
    batteria = models.BooleanField(default=False)
    banco = models.BooleanField(default=False)
    prima = models.DateTimeField(null=True, blank=True)
    codice = models.CharField(max_length=50, blank=True, default="", help_text="Codice dell'elemento (internalCode)")
    elaborata = models.BooleanField(default=False)
    scartata = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"codifica {self.id_metodo}"
