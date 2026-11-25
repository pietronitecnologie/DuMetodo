"""Clienti committenti (Metodo v3): l'azienda produce per più clienti, ognuno con il suo flusso (es. DMG, ABB).

Ogni ordine appartiene a un committente. Il committente **predefinito** è quello usato quando non è indicato
(importazioni, dati storici, ordini creati da codice che non lo specifica).
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from simple_history.models import HistoricalRecords

from metodo.core.models import ModelloTemporale


class Committente(ModelloTemporale):
    codice = models.CharField(
        "codice", max_length=20, unique=True, help_text="Breve e stabile, es. DMG o ABB (maiuscole, cifre, - e _)."
    )
    nome = models.CharField("nome", max_length=100)
    colore = models.CharField(
        "colore", max_length=7, default="#2f74f0", help_text="Per riconoscerlo a colpo d'occhio nel selettore."
    )
    predefinito = models.BooleanField(
        "predefinito", default=False, help_text="Usato quando il committente non è indicato (es. importazioni)."
    )
    attivo = models.BooleanField("attivo", default=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "cliente committente"
        verbose_name_plural = "clienti committenti"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(fields=["predefinito"], condition=Q(predefinito=True), name="un_solo_predefinito"),
        ]

    def __str__(self) -> str:
        return self.nome


def predefinito_id() -> int:
    """Default di ``Ordine.committente``: il predefinito (creato al primo uso se non esiste ancora)."""
    c = Committente.objects.filter(predefinito=True).first() or Committente.objects.order_by("pk").first()
    if c is None:
        c = Committente.objects.create(codice="CLIENTE", nome="Cliente", predefinito=True)
    return c.pk
