"""Consegne (bolle) e DDT.

In Metodo la consegna era una GET che scriveva il file al volo: un doppio clic, un prefetch del
browser o due utenti insieme potevano generare bolle doppie o segnare righe mai scaricate. Il file
non veniva conservato. Le righe consegnate finivano in ``DocumentiTrasporto`` legate all'ordine
per stringhe, e il numero DDT era libero (riutilizzabile, senza anno).

Ora: ``Consegna`` conserva il file generato (riscaricabile); ``RigaConsegnata`` collega la riga
d'ordine (e quindi l'ordine) con una ForeignKey e ne fotografa quantità e prezzo; ``DDT`` ha
numero+anno univoci e conserva il file tecnico inviato.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.urls import reverse


class Consegna(models.Model):
    creata_il = models.DateTimeField(auto_now_add=True)
    creata_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    selezione = models.CharField(max_length=100, blank=True, default="")
    tracciato = models.CharField("tracciato usato", max_length=100, blank=True, default="")
    file_nome = models.CharField(max_length=200)
    file_contenuto = models.BinaryField()

    class Meta:
        verbose_name = "consegna"
        verbose_name_plural = "consegne"
        ordering = ["-creata_il", "-id"]

    def __str__(self) -> str:
        return f"Consegna {self.pk} del {self.creata_il:%d/%m/%Y %H:%M}"

    def get_absolute_url(self) -> str:
        return reverse("consegne:dettaglio", args=[self.pk])


class OrigineDDT(models.TextChoices):
    ESTERNO = "EST", "Numero dal gestionale esterno"
    DUMETODO = "DUM", "Emesso da DuMetodo"


class DDT(models.Model):
    """DDT delle righe consegnate.

    Due origini: il numero scritto a mano dal gestionale esterno (Spedizioni → DDT) oppure il documento di
    trasporto EMESSO da DuMetodo (Gestionale → DDT), con numerazione progressiva per anno e i dati di legge
    (DPR 472/1996): mittente, destinatario, destinazione e righe fotografati all'emissione, causale,
    aspetto dei beni, colli, peso, trasporto, porto. In entrambi i casi il file tecnico è lo stesso.
    """

    numero = models.CharField("numero DDT", max_length=20)
    anno = models.PositiveSmallIntegerField("anno")
    data = models.DateField("data DDT")
    creato_il = models.DateTimeField(auto_now_add=True)
    creato_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    file_nome = models.CharField(max_length=200, blank=True, default="")
    file_contenuto = models.BinaryField(default=b"")
    email = models.ForeignKey(
        "comunicazioni.EmailInUscita", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    origine = models.CharField(max_length=3, choices=OrigineDDT.choices, default=OrigineDDT.ESTERNO)
    # --- dati di legge (solo DDT emessi da DuMetodo)
    progressivo = models.PositiveIntegerField("progressivo dell'anno", null=True, blank=True)
    numerazione = models.ForeignKey(
        "gestionale.Numerazione", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # Fotografia all'emissione: mittente, destinatario, destinazione e righe stampate (il documento di legge
    # non cambia se poi cambiano anagrafiche o configurazione).
    documento = models.JSONField("dati del documento all'emissione", default=dict, blank=True)
    causale = models.CharField("causale del trasporto", max_length=60, blank=True, default="")
    aspetto_beni = models.CharField("aspetto esteriore dei beni", max_length=100, blank=True, default="")
    colli = models.PositiveIntegerField("numero di colli", null=True, blank=True)
    peso_kg = models.DecimalField("peso (kg)", max_digits=10, decimal_places=2, null=True, blank=True)
    trasporto_a_cura = models.CharField("trasporto a cura di", max_length=20, blank=True, default="")
    vettore = models.CharField("vettore", max_length=150, blank=True, default="")
    vettore_indirizzo = models.CharField("indirizzo del vettore", max_length=200, blank=True, default="")
    porto = models.CharField("porto", max_length=20, blank=True, default="")
    inizio_trasporto = models.DateTimeField("inizio del trasporto", null=True, blank=True)
    note = models.TextField("annotazioni", blank=True, default="")
    annullato_il = models.DateTimeField(null=True, blank=True)
    annullato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    motivo_annullamento = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        verbose_name = "DDT"
        verbose_name_plural = "DDT"
        ordering = ["-anno", "-data", "-id"]
        constraints = [models.UniqueConstraint(fields=["numero", "anno"], name="ddt_numero_anno_unico")]

    def __str__(self) -> str:
        return f"DDT {self.numero}/{self.anno}"

    @property
    def emesso_da_dumetodo(self) -> bool:
        return self.origine == OrigineDDT.DUMETODO

    @property
    def annullato(self) -> bool:
        return self.annullato_il is not None


class RigaConsegnata(models.Model):
    consegna = models.ForeignKey(Consegna, on_delete=models.PROTECT, related_name="righe")
    riga = models.OneToOneField("ordini.RigaOrdine", on_delete=models.PROTECT, related_name="consegnata")
    qta = models.DecimalField(max_digits=14, decimal_places=3)
    prezzo_unitario = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    ddt = models.ForeignKey(DDT, on_delete=models.PROTECT, null=True, blank=True, related_name="righe")

    class Meta:
        verbose_name = "riga consegnata"
        verbose_name_plural = "righe consegnate"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.riga} (consegna {self.consegna_id})"
