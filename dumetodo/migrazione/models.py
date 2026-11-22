"""Stato del prelievo a blocchi da Metodo: permette di interromperlo e riprenderlo in sicurezza."""

from django.db import models


class Avanzamento(models.Model):
    """A che punto è un passo del prelievo: l'ultimo id di Metodo già salvato in DuMetodo."""

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
    """Record di Metodo -> oggetto DuMetodo: evita doppioni se un blocco viene riletto."""

    tabella = models.CharField(max_length=40)
    id_metodo = models.BigIntegerField()
    id_dumetodo = models.BigIntegerField()
    creato = models.BooleanField(default=True, help_text="Falso se l'oggetto esisteva già in DuMetodo")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tabella", "id_metodo"], name="corrispondenza_unica")]
        indexes = [models.Index(fields=["tabella", "id_dumetodo"], name="corrispondenza_dumetodo_idx")]

    def __str__(self) -> str:
        return f"{self.tabella} {self.id_metodo} -> {self.id_dumetodo}"


class CodificaMetodo(models.Model):
    """Riepilogo per codice tracciato (Plan_coding) costruito leggendo le tracce di Metodo."""

    id_metodo = models.BigIntegerField(unique=True)
    batteria = models.BooleanField(default=False)
    banco = models.BooleanField(default=False)
    prima = models.DateTimeField(null=True, blank=True)
    elemento_id = models.BigIntegerField(null=True, blank=True)
    elaborata = models.BooleanField(default=False)
    scartata = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"codifica {self.id_metodo}"
