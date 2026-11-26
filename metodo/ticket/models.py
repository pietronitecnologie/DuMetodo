"""Ticket: segnalazioni aperte in produzione (anche da un ordine), seguite fino alla chiusura con le ore impiegate.

Le ore si misurano dall'apertura alla chiusura; il tempo passato «in attesa» (di materiale, del cliente…) si tiene a
parte, così le ore effettive dicono quanto ci si è messo davvero. ``preso_in_carico_il`` dà il tempo di risposta.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from simple_history.models import HistoricalRecords


class Stato(models.TextChoices):
    APERTO = "APE", "Aperto"
    LAVORAZIONE = "LAV", "In lavorazione"
    ATTESA = "ATT", "In attesa"
    CHIUSO = "CHI", "Chiuso"


class Priorita(models.TextChoices):
    BASSA = "1", "Bassa"
    NORMALE = "2", "Normale"
    ALTA = "3", "Alta"
    URGENTE = "4", "Urgente"


class Categoria(models.TextChoices):
    QUALITA = "QUA", "Qualità / non conformità"
    MATERIALE = "MAT", "Materiale mancante o difettoso"
    MACCHINA = "MAC", "Macchina o attrezzatura"
    DOCUMENTI = "DOC", "Disegni e documentazione"
    CLIENTE = "CLI", "Richiesta del cliente"
    ALTRO = "ALT", "Altro"


class TicketQuerySet(models.QuerySet["Ticket"]):
    def aperti(self) -> TicketQuerySet:
        return self.exclude(stato=Stato.CHIUSO)


class Ticket(models.Model):
    titolo = models.CharField("titolo", max_length=200)
    descrizione = models.TextField("descrizione", blank=True)
    categoria = models.CharField("categoria", max_length=3, choices=Categoria.choices, default=Categoria.ALTRO)
    priorita = models.CharField("priorità", max_length=1, choices=Priorita.choices, default=Priorita.NORMALE)
    stato = models.CharField("stato", max_length=3, choices=Stato.choices, default=Stato.APERTO, db_index=True)
    ordine = models.ForeignKey(
        "ordini.Ordine", on_delete=models.SET_NULL, null=True, blank=True, related_name="ticket", verbose_name="ordine"
    )
    riga = models.ForeignKey(
        "ordini.RigaOrdine",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ticket",
        verbose_name="riga dell'ordine",
    )
    aperto_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="ticket_aperti"
    )
    aperto_il = models.DateTimeField("aperto il", default=timezone.now, db_index=True)
    assegnato_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ticket_assegnati",
        verbose_name="assegnato a",
    )
    preso_in_carico_il = models.DateTimeField("preso in carico il", null=True, blank=True)
    in_attesa_dal = models.DateTimeField(null=True, blank=True)
    secondi_attesa = models.PositiveIntegerField(default=0)  # attese concluse (quella in corso è in_attesa_dal)
    chiuso_il = models.DateTimeField("chiuso il", null=True, blank=True)
    chiuso_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="ticket_chiusi"
    )
    soluzione = models.TextField("soluzione", blank=True)

    objects = TicketQuerySet.as_manager()
    history = HistoricalRecords()

    class Meta:
        ordering = ["-aperto_il"]
        verbose_name = "ticket"
        verbose_name_plural = "ticket"

    def __str__(self) -> str:
        return f"#{self.pk} {self.titolo}"

    def get_absolute_url(self) -> str:
        return reverse("ticket:dettaglio", args=[self.pk])

    @property
    def is_chiuso(self) -> bool:
        return self.stato == Stato.CHIUSO


class TipoEvento(models.TextChoices):
    APERTURA = "APE", "Apertura"
    COMMENTO = "COM", "Commento"
    STATO = "STA", "Cambio di stato"
    ASSEGNAZIONE = "ASS", "Assegnazione"
    PRIORITA = "PRI", "Priorità"


class EventoTicket(models.Model):
    """La cronologia del ticket: commenti e cambi (stato, assegnazione, priorità)."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="eventi")
    tipo = models.CharField(max_length=3, choices=TipoEvento.choices)
    autore = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    momento = models.DateTimeField(default=timezone.now)
    testo = models.TextField(blank=True)

    class Meta:
        ordering = ["momento", "pk"]

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} #{self.ticket_id}"
