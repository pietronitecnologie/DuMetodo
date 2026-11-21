"""Gestionale: numerazione di legge, fatture elettroniche (FatturaPA) e note di credito.

I DDT sono ``consegne.DDT`` (con i dati di legge quando li emette DuMetodo): così consegna, file tecnico e
fatturazione lavorano sulle stesse righe consegnate.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse

from .costanti import TIPI_DOCUMENTO


class TipoNumerazione(models.TextChoices):
    DDT = "DDT", "DDT"
    FATTURA = "FAT", "Fatture e note di credito"


class Numerazione(models.Model):
    """Una serie di numerazione (sezionale): es. «Fatture» {numero}/{anno}, «Fatture estero» {numero}/E.

    Il progressivo riparte da 1 ogni anno (o dal numero di partenza indicato per quell'anno, vedi Numeratore).
    Più numerazioni dello stesso tipo devono avere formati diversi: due documenti dello stesso anno non
    possono avere lo stesso numero."""

    tipo = models.CharField(max_length=3, choices=TipoNumerazione.choices)
    nome = models.CharField(max_length=60)
    formato = models.CharField(
        max_length=30,
        default="{numero}",
        help_text="{numero} = progressivo dell'anno, {anno} = anno. Es. {numero}/{anno}.",
    )
    predefinita = models.BooleanField(default=False)
    attiva = models.BooleanField(default=True)

    class Meta:
        verbose_name = "numerazione"
        verbose_name_plural = "numerazioni"
        ordering = ["tipo", "-predefinita", "nome"]
        constraints = [
            models.UniqueConstraint(fields=["tipo", "nome"], name="numerazione_nome_unico"),
            models.UniqueConstraint(fields=["tipo", "formato"], name="numerazione_formato_unico"),
            models.UniqueConstraint(fields=["tipo"], condition=Q(predefinita=True), name="numerazione_una_predefinita"),
        ]

    def __str__(self) -> str:
        return self.nome

    def numero(self, progressivo: int, anno: int) -> str:
        return self.formato.format(numero=progressivo, anno=anno)


class Numeratore(models.Model):
    """Ultimo numero usato per numerazione e anno (riga bloccata durante l'emissione: niente buchi né doppi).

    Per far partire la numerazione da un numero (es. 10, perché fino al 9 si è fatturato con un altro programma)
    si imposta ``ultimo`` = 9."""

    numerazione = models.ForeignKey(Numerazione, on_delete=models.PROTECT, related_name="contatori")
    anno = models.PositiveSmallIntegerField()
    ultimo = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "numeratore"
        verbose_name_plural = "numeratori"
        constraints = [
            models.UniqueConstraint(fields=["numerazione", "anno"], name="numeratore_numerazione_anno_unico")
        ]

    def __str__(self) -> str:
        return f"{self.numerazione} {self.anno}: {self.ultimo}"


class StatoFattura(models.TextChoices):
    BOZZA = "BOZ", "Bozza"
    EMESSA = "EME", "Emessa"


class StatoSdI(models.TextChoices):
    DA_INVIARE = "DAI", "Da inviare"
    INVIATA = "INV", "Inviata allo SdI"
    CONSEGNATA = "CON", "Consegnata al cliente"
    SCARTATA = "SCA", "Scartata dallo SdI"


class Fattura(models.Model):
    tipo = models.CharField(max_length=4, choices=TIPI_DOCUMENTO, default="TD24")
    stato = models.CharField(max_length=3, choices=StatoFattura.choices, default=StatoFattura.BOZZA, db_index=True)
    data = models.DateField("data")
    anno = models.PositiveSmallIntegerField("anno")
    progressivo = models.PositiveIntegerField("progressivo dell'anno", null=True, blank=True)
    numero = models.CharField("numero", max_length=20, blank=True, default="")
    numerazione = models.ForeignKey(Numerazione, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    fattura_collegata = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="note_di_credito"
    )
    # Fotografie all'emissione (in bozza si leggono dalla configurazione).
    cedente = models.JSONField(default=dict, blank=True)
    cessionario = models.JSONField(default=dict, blank=True)
    pagamento = models.JSONField(default=dict, blank=True)
    causale = models.CharField("causale", max_length=200, blank=True, default="")
    imponibile = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    imposta = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    totale = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    scadenza = models.DateField("scadenza del pagamento", null=True, blank=True)
    progressivo_invio = models.CharField(max_length=5, blank=True, default="")
    xml_nome = models.CharField(max_length=60, blank=True, default="")
    xml = models.TextField(blank=True, default="")
    stato_sdi = models.CharField(max_length=3, choices=StatoSdI.choices, default=StatoSdI.DA_INVIARE)
    note_sdi = models.CharField("note sull'invio", max_length=300, blank=True, default="")
    creata_il = models.DateTimeField(auto_now_add=True)
    creata_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    emessa_il = models.DateTimeField(null=True, blank=True)
    emessa_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "fattura"
        verbose_name_plural = "fatture"
        ordering = ["-anno", "-progressivo", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["numerazione", "anno", "progressivo"],
                condition=Q(progressivo__isnull=False),
                name="fattura_numero_unico",
            ),
            models.CheckConstraint(
                condition=Q(stato="BOZ") | Q(progressivo__isnull=False), name="fattura_emessa_numerata"
            ),
        ]

    def __str__(self) -> str:
        nome = self.get_tipo_display()
        return f"{nome} {self.numero} del {self.data:%d/%m/%Y}" if self.numero else f"{nome} (bozza {self.pk})"

    def get_absolute_url(self) -> str:
        return reverse("gestionale:fattura", args=[self.pk])

    @property
    def is_bozza(self) -> bool:
        return self.stato == StatoFattura.BOZZA

    @property
    def is_nota_di_credito(self) -> bool:
        return self.tipo == "TD04"


class RigaFattura(models.Model):
    fattura = models.ForeignKey(Fattura, on_delete=models.CASCADE, related_name="righe")
    n_linea = models.PositiveIntegerField("numero linea")
    ddt = models.ForeignKey(
        "consegne.DDT", on_delete=models.PROTECT, null=True, blank=True, related_name="righe_fattura"
    )
    riga_consegnata = models.ForeignKey(
        "consegne.RigaConsegnata", on_delete=models.PROTECT, null=True, blank=True, related_name="righe_fattura"
    )
    codice = models.CharField(max_length=50, blank=True, default="")
    descrizione = models.CharField(max_length=1000)
    qta = models.DecimalField(max_digits=14, decimal_places=3)
    um = models.CharField(max_length=10, blank=True, default="")
    prezzo_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    sconto_unitario = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    totale = models.DecimalField(max_digits=14, decimal_places=2)
    aliquota = models.DecimalField(max_digits=5, decimal_places=2)
    natura = models.CharField(max_length=5, blank=True, default="")

    class Meta:
        verbose_name = "riga fattura"
        verbose_name_plural = "righe fattura"
        ordering = ["n_linea", "id"]
        constraints = [models.UniqueConstraint(fields=["fattura", "n_linea"], name="riga_fattura_linea_unica")]

    def __str__(self) -> str:
        return f"{self.fattura} linea {self.n_linea}"
