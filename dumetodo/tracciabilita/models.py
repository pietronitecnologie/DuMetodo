"""Archivio delle tracce (in sola lettura): eventi registrati in passato o importati da Metodo.

Le nuove registrazioni (controlli qualità, lotti, foto e file, operazioni) si fanno con il modulo
Tracciatura; i vecchi controlli qualità e i file degli ordini sono stati convertiti in tracciature.

Differenze rispetto a Metodo:
- ``Elemento`` sostituisce la coppia ``Plan.Coding`` + ``Stored_Item`` (un record per elemento fisico,
  non uno per evento); il codice è UNICO a livello di database;
- ogni traccia legata a un ordine ha una ForeignKey verso ``Ordine`` (in Metodo: stringhe e ``contains``);
- batterie, cavi, postazioni (banchi) e work order batterie sono stati eliminati: non più usati.
"""

from __future__ import annotations

import uuid
from pathlib import PurePath

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse

from dumetodo.core.models import ModelloTemporale


def _percorso(cartella: str, nome: str) -> str:
    """Nome di file casuale (mai quello scelto dall'utente) dentro una cartella non pubblica."""
    estensione = PurePath(nome).suffix.lower()[:10]
    return f"{cartella}/{uuid.uuid4().hex}{estensione}"


# =========================================================================== elementi e tracce
class Elemento(ModelloTemporale):
    """Elemento fisico tracciato, identificato da un codice univoco (ex ``Coding`` + ``Stored_Item``)."""

    codice = models.CharField("codice", max_length=50, unique=True)
    creato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "elemento tracciato"
        verbose_name_plural = "elementi tracciati"
        ordering = ["codice"]

    def __str__(self) -> str:
        return self.codice


class Chiave:
    """Chiavi riservate degli eventi (le tracce generiche usano chiavi libere)."""

    LOTTO = "LOTTO"
    BUSTA = "BUSTA"

    RISERVATE = frozenset({LOTTO, BUSTA})
    ETICHETTE: dict[str, str] = {}


class Traccia(models.Model):
    """Evento di tracciabilità: "l'elemento X / l'ordine Y aveva ``chiave`` = ``valore`` al momento ``data``"."""

    elemento = models.ForeignKey(Elemento, on_delete=models.PROTECT, null=True, blank=True, related_name="tracce")
    ordine = models.ForeignKey("ordini.Ordine", on_delete=models.PROTECT, null=True, blank=True, related_name="tracce")
    data = models.DateTimeField("data evento")
    chiave = models.CharField("chiave", max_length=50)
    valore = models.CharField("valore", max_length=100, blank=True, default="")
    reparto = models.ForeignKey("core.Reparto", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    # Data di inserimento vera (in Metodo era auto_now: cambiava a ogni salvataggio).
    inserita_il = models.DateTimeField("inserita il", auto_now_add=True)
    inserita_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "traccia"
        verbose_name_plural = "tracce"
        ordering = ["-data", "-id"]
        indexes = [
            models.Index(fields=["data"], name="traccia_data_idx"),
            models.Index(fields=["chiave", "data"], name="traccia_chiave_data_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(elemento__isnull=False) | Q(ordine__isnull=False), name="traccia_elemento_o_ordine"
            ),
        ]

    def __str__(self) -> str:
        soggetto = self.elemento.codice if self.elemento_id else str(self.ordine)
        return f"{soggetto}: {self.chiave}={self.valore}"

    def get_absolute_url(self) -> str:
        return reverse("tracciabilita:traccia", args=[self.pk])

    @property
    def chiave_leggibile(self) -> str:
        return Chiave.ETICHETTE.get(self.chiave, self.chiave)

    @property
    def testo_barcode(self) -> str:
        """Contenuto del codice a barre (Code128), come in Metodo: testo CSV."""
        from django.utils import timezone

        data = timezone.localtime(self.data).strftime("%d/%m/%Y")
        codice = self.elemento.codice if self.elemento_id else ""
        return f"{codice},{self.chiave},{self.valore},{data}"


# =========================================================================== file per ordine
def percorso_file_ordine(instance: object, nome: str) -> str:
    """Conservata perché citata dalle migrazioni: foto e file ora sono campi delle Tracciature."""
    return _percorso(f"ordini/{instance.ordine_id}", nome)


def percorso_file_reclamo(instance: object, nome: str) -> str:
    """Conservata solo perché citata dalla migrazione 0001: i reclami sono stati eliminati."""
    return _percorso("reclami", nome)
