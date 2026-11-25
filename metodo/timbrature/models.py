"""Timbrature: lavoratori con un codice personale, postazioni (nel client desktop) e timbrature.

Una postazione SENZA reparto è l'ingresso/uscita dello stabilimento; una postazione di reparto registra
l'inizio e la fine del lavoro in quel reparto. Le ore per reparto (e quindi i costi) si ricavano dalla
sequenza delle timbrature: vedi ``calcolo.py``.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.urls import reverse
from simple_history.models import HistoricalRecords

from metodo.core.models import ModelloTemporale


class Lavoratore(ModelloTemporale):
    nome = models.CharField("nome", max_length=60)
    cognome = models.CharField("cognome", max_length=60)
    matricola = models.CharField("matricola", max_length=30, unique=True)
    reparto = models.ForeignKey(
        "core.Reparto",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="reparto abituale",
    )
    costo_orario = models.DecimalField(
        "costo orario (€)",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Costo aziendale di un'ora di lavoro: serve alle metriche di costo per reparto.",
    )
    # Il codice personale non si conserva in chiaro: solo la sua impronta (HMAC), che basta per riconoscerlo.
    codice_impronta = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)
    codice_impostato_il = models.DateTimeField(null=True, blank=True, editable=False)
    attivo = models.BooleanField("attivo", default=True)
    note = models.CharField("note", max_length=200, blank=True, default="")

    history = HistoricalRecords()

    class Meta:
        verbose_name = "lavoratore"
        verbose_name_plural = "lavoratori"
        ordering = ["cognome", "nome"]

    def __str__(self) -> str:
        return f"{self.cognome} {self.nome}"

    def get_absolute_url(self) -> str:
        return reverse("timbrature:lavoratore", args=[self.pk])


class TipoIdentificativo(models.TextChoices):
    BADGE = "BDG", "Badge (codice a barre / QR)"
    NFC = "NFC", "Tessera NFC / RFID"


class Identificativo(models.Model):
    """Badge o tessera del lavoratore, letti alla postazione da un lettore (barcode, QR, NFC) o dalla fotocamera.

    Il valore è quello stampato sul badge o scritto nella tessera: non è un segreto (è sul supporto fisico) e
    si conserva in chiaro, normalizzato, per poter ristampare il badge. Il codice personale da digitare invece
    resta solo come impronta (``Lavoratore.codice_impronta``)."""

    lavoratore = models.ForeignKey(Lavoratore, on_delete=models.CASCADE, related_name="identificativi")
    tipo = models.CharField(max_length=3, choices=TipoIdentificativo.choices)
    valore = models.CharField(max_length=64, unique=True)
    descrizione = models.CharField(max_length=60, blank=True, default="")
    attivo = models.BooleanField(default=True)
    creato_il = models.DateTimeField(auto_now_add=True)
    ultimo_uso = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "badge o tessera"
        verbose_name_plural = "badge e tessere"
        ordering = ["lavoratore", "tipo", "creato_il"]

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} {self.valore}"


class Postazione(ModelloTemporale):
    nome = models.CharField("nome", max_length=60, unique=True)
    reparto = models.ForeignKey(
        "core.Reparto",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Vuoto = postazione di ingresso e uscita dallo stabilimento.",
    )
    token = models.CharField(max_length=64, unique=True, editable=False)
    attiva = models.BooleanField("attiva", default=True)
    # Come ci si identifica alla postazione.
    usa_tastierino = models.BooleanField("tastierino sullo schermo", default=True)
    usa_lettori = models.BooleanField(
        "tastiera e lettori USB (codici a barre, QR, tessere NFC)",
        default=True,
        help_text="I lettori USB scrivono il codice letto come una tastiera, seguito da Invio.",
    )
    usa_fotocamera = models.BooleanField(
        "fotocamera per i QR", default=False, help_text="Per tablet o PC con webcam: si inquadra il QR del badge."
    )
    ultimo_contatto = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "postazione"
        verbose_name_plural = "postazioni"
        ordering = ["reparto__nome", "nome"]

    def __str__(self) -> str:
        return self.nome

    @property
    def is_ingresso(self) -> bool:
        return self.reparto_id is None


class TipoTimbratura(models.TextChoices):
    ENTRATA = "ENT", "Entrata"
    USCITA = "USC", "Uscita"
    INIZIO_REPARTO = "IRP", "Inizio lavoro in reparto"
    FINE_REPARTO = "FRP", "Fine lavoro in reparto"
    INIZIO_MENSA = "IME", "Inizio mensa"
    FINE_MENSA = "FME", "Fine mensa"
    INIZIO_PAUSA = "IPA", "Inizio pausa"
    FINE_PAUSA = "FPA", "Fine pausa"


class OrigineTimbratura(models.TextChoices):
    POSTAZIONE = "POS", "Postazione"
    MANUALE = "MAN", "Inserita a mano"


class Timbratura(models.Model):
    lavoratore = models.ForeignKey(Lavoratore, on_delete=models.PROTECT, related_name="timbrature")
    tipo = models.CharField(max_length=3, choices=TipoTimbratura.choices)
    momento = models.DateTimeField(db_index=True)
    postazione = models.ForeignKey(Postazione, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reparto = models.ForeignKey("core.Reparto", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    origine = models.CharField(max_length=3, choices=OrigineTimbratura.choices, default=OrigineTimbratura.POSTAZIONE)
    nota = models.CharField(max_length=200, blank=True, default="")
    creata_il = models.DateTimeField(auto_now_add=True)
    creata_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    annullata_il = models.DateTimeField(null=True, blank=True)
    annullata_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    motivo_annullamento = models.CharField(max_length=200, blank=True, default="")

    history = HistoricalRecords()

    class Meta:
        verbose_name = "timbratura"
        verbose_name_plural = "timbrature"
        ordering = ["momento", "id"]
        indexes = [models.Index(fields=["lavoratore", "momento"])]

    def __str__(self) -> str:
        return f"{self.lavoratore} {self.get_tipo_display()} {self.momento:%d/%m/%Y %H:%M}"

    @property
    def annullata(self) -> bool:
        return self.annullata_il is not None
