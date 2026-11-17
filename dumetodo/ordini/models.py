"""Ordini (commesse) e righe.

Differenze principali rispetto a Metodo:
- ``RigaOrdine`` ha una vera ForeignKey verso ``Ordine`` (prima si univano per 4 stringhe);
- la chiave (serie, numero, appendice, anno) è UNICA a livello di database;
- prezzi e quantità sono Decimal (prima stringhe tipo "0,2596");
- i flag sono booleani NOT NULL; "annullata"/"duplicata" diventano uno stato della riga;
- ogni modifica è storicizzata con l'utente che l'ha fatta (django-simple-history).
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse
from simple_history.models import HistoricalRecords

from dumetodo.core.models import ModelloTemporale


class StatoOrdine(models.TextChoices):
    INSERITO = "INS", "Inserito"
    IN_LAVORAZIONE = "LAV", "In lavorazione"
    IN_SPEDIZIONE = "SPE", "In spedizione"
    EVASIONE_PARZIALE = "SPP", "Evasione parziale"
    CONSEGNATO = "CON", "Consegnato"
    ANNULLATO = "ANN", "Annullato"


class Urgenza(models.TextChoices):
    NORMALE = "NRM", "Normale"
    ALTA = "ALT", "Alta"
    H24 = "24", "24 ore"


class TipoOrdine(models.TextChoices):
    ASSEMBLAGGIO = "ASM", "Assemblaggio"
    PROJECT_MANAGEMENT = "PM", "Project Management"
    COMMISSIONING = "CMS", "Commissioning"
    SERVIZIO = "SRV", "Servizio"
    INGEGNERIA = "ING", "Ingegneria"
    TRASPORTO = "TRA", "Trasporto"
    PULIZIA = "PUL", "Pulizia"


class OrdineQuerySet(models.QuerySet["Ordine"]):
    def aperti(self) -> OrdineQuerySet:
        return self.exclude(stato__in=[StatoOrdine.CONSEGNATO, StatoOrdine.ANNULLATO])

    def per_chiave(self, serie: str, numero: str, appendice: str, anno: int) -> OrdineQuerySet:
        return self.filter(serie=serie, numero=numero, appendice=appendice, anno=anno)


class Ordine(ModelloTemporale):
    serie = models.CharField("serie", max_length=5, help_text="Es. 00040")
    numero = models.CharField("numero ordine", max_length=30, help_text="Es. 0004300")
    appendice = models.CharField(
        "appendice",
        max_length=10,
        blank=True,
        default="",
        help_text="Vuota per gli ordini del cliente; T1/T2 pulsanti, R1/R2 elettronica, CL conto lavoro.",
    )
    anno = models.PositiveSmallIntegerField("anno")

    stato = models.CharField("stato", max_length=3, choices=StatoOrdine.choices, default=StatoOrdine.INSERITO)
    urgenza = models.CharField("urgenza", max_length=3, choices=Urgenza.choices, default=Urgenza.NORMALE)
    tipo = models.CharField("tipo", max_length=3, choices=TipoOrdine.choices, default=TipoOrdine.ASSEMBLAGGIO)
    reparto = models.ForeignKey("core.Reparto", on_delete=models.PROTECT, null=True, blank=True, related_name="ordini")
    cliente = models.CharField("codice cliente", max_length=30, blank=True, default="")
    data_scadenza = models.DateField("data scadenza", null=True, blank=True)
    data_consegna = models.DateField("data consegna", null=True, blank=True)

    history = HistoricalRecords()
    objects = OrdineQuerySet.as_manager()

    class Meta:
        verbose_name = "ordine"
        verbose_name_plural = "ordini"
        ordering = ["-anno", "serie", "numero", "appendice"]
        constraints = [
            models.UniqueConstraint(fields=["serie", "numero", "appendice", "anno"], name="ordine_chiave_unica"),
            models.CheckConstraint(condition=Q(anno__gte=2000) & Q(anno__lte=2100), name="ordine_anno_valido"),
        ]
        indexes = [
            models.Index(fields=["stato", "data_scadenza"], name="ordine_stato_scad_idx"),
            models.Index(fields=["numero"], name="ordine_numero_idx"),
        ]

    def __str__(self) -> str:
        return self.codice

    @property
    def codice(self) -> str:
        """Formato completo di Metodo: ``serie.numero[.appendice]-anno``."""
        base = f"{self.serie}.{self.numero}"
        if self.appendice:
            base += f".{self.appendice}"
        return f"{base}-{self.anno}"

    @property
    def codice_breve(self) -> str:
        """Formato delle liste di Metodo: ``40.0004300[-T1]-2026``."""
        base = f"{self.serie[-2:]}.{self.numero}"
        if self.appendice:
            base += f"-{self.appendice}"
        return f"{base}-{self.anno}"

    @property
    def chiave_ticket(self) -> str:
        """Formato usato dall'app Tickets di Metodo (``serie.numero.appendice.anno``)."""
        return f"{self.serie}.{self.numero}.{self.appendice or '0'}.{self.anno}"

    @property
    def is_chiuso(self) -> bool:
        return self.stato in (StatoOrdine.CONSEGNATO, StatoOrdine.ANNULLATO)

    @property
    def reparto_da_appendice(self) -> str:
        app = self.appendice.upper()
        if app == "CL":
            return "Conto lavoro"
        if "T" in app:
            return "Pulsanti"
        if "R" in app:
            return "Elettronica"
        return ""

    def get_absolute_url(self) -> str:
        return reverse("ordini:dettaglio", args=[self.pk])


class StatoRiga(models.TextChoices):
    ATTIVA = "ATT", "Attiva"
    ANNULLATA = "ANN", "Annullata"
    SOSTITUITA = "SOS", "Sostituita"  # ex "duplicata": stesso n. riga, articolo diverso


class OrigineRiga(models.TextChoices):
    IMPORTAZIONE = "IMP", "Importazione"
    MANUALE = "MAN", "Inserimento manuale"
    DIVISIONE = "DIV", "Divisione riga"


class RigaQuerySet(models.QuerySet["RigaOrdine"]):
    def attive(self) -> RigaQuerySet:
        return self.filter(stato=StatoRiga.ATTIVA)


class RigaOrdine(ModelloTemporale):
    ordine = models.ForeignKey(Ordine, on_delete=models.PROTECT, related_name="righe")
    n_riga = models.CharField("n. riga", max_length=30)
    articolo = models.CharField("articolo", max_length=50, db_index=True)
    variante = models.CharField("variante", max_length=20, blank=True, default="")
    qta = models.DecimalField("quantità", max_digits=14, decimal_places=3)
    prezzo_unitario = models.DecimalField("prezzo unitario", max_digits=14, decimal_places=4, null=True, blank=True)
    sconto = models.DecimalField("sconto unitario", max_digits=14, decimal_places=4, null=True, blank=True)
    cliente = models.CharField("codice cliente", max_length=30, blank=True, default="")
    magazzino = models.CharField("magazzino", max_length=20, blank=True, default="")
    data_scadenza = models.DateField("data scadenza", null=True, blank=True)
    data_consegna = models.DateField("data consegna", null=True, blank=True)

    stato = models.CharField("stato riga", max_length=3, choices=StatoRiga.choices, default=StatoRiga.ATTIVA)
    bloccata = models.BooleanField("bloccata", default=False)
    in_evasione = models.BooleanField("in evasione", default=False)
    bolla_emessa = models.BooleanField("bolla emessa", default=False)
    scorporata = models.BooleanField("scorporata (non fatturare)", default=False)

    origine = models.CharField("origine", max_length=3, choices=OrigineRiga.choices, default=OrigineRiga.IMPORTAZIONE)
    riga_origine = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="divisioni",
        help_text="Per le righe nate da una divisione: la riga da cui derivano.",
    )
    ultimo_file_importato = models.ForeignKey(
        "importazione.FileImportato", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    history = HistoricalRecords()
    objects = RigaQuerySet.as_manager()

    class Meta:
        verbose_name = "riga ordine"
        verbose_name_plural = "righe ordine"
        ordering = ["n_riga", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["ordine", "n_riga"], condition=Q(stato="ATT"), name="riga_attiva_unica_per_numero"
            ),
            models.CheckConstraint(condition=Q(qta__gte=0), name="riga_qta_non_negativa"),
            models.CheckConstraint(
                condition=Q(prezzo_unitario__isnull=True) | Q(prezzo_unitario__gte=0), name="riga_prezzo_non_negativo"
            ),
            models.CheckConstraint(
                condition=~(Q(bloccata=True) & Q(in_evasione=True)), name="riga_bloccata_xor_evasione"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.ordine.codice} riga {self.n_riga}: {self.articolo} × {self.qta}"

    @property
    def importo(self) -> Decimal | None:
        if self.prezzo_unitario is None:
            return None
        return (self.qta * self.prezzo_unitario).quantize(Decimal("0.01"))

    @property
    def is_attiva(self) -> bool:
        return self.stato == StatoRiga.ATTIVA


class OrigineModificaScadenza(models.TextChoices):
    UTENTE = "UTE", "Modifica utente"
    IMPORTAZIONE = "IMP", "Importazione"
    DIVISIONE = "DIV", "Divisione riga"


class StoricoScadenza(models.Model):
    """Ogni cambio di data di scadenza, di ordine o di riga, con motivo e autore.

    In Metodo veniva scritto solo da 'Cambia ordine': le variazioni arrivate con le
    reimportazioni andavano perse. Ora è registrato tutto.
    """

    ordine = models.ForeignKey(Ordine, on_delete=models.CASCADE, related_name="storico_scadenze")
    riga = models.ForeignKey(
        RigaOrdine, on_delete=models.CASCADE, null=True, blank=True, related_name="storico_scadenze"
    )
    precedente = models.DateField("scadenza precedente", null=True, blank=True)
    nuova = models.DateField("nuova scadenza", null=True, blank=True)
    origine = models.CharField(max_length=3, choices=OrigineModificaScadenza.choices)
    motivo = models.TextField("motivo", blank=True, default="")
    utente = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "variazione scadenza"
        verbose_name_plural = "storico scadenze"
        ordering = ["-creato_il", "-id"]

    def __str__(self) -> str:
        return f"{self.ordine}: {self.precedente} → {self.nuova}"
