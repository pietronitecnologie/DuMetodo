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


class DDT(models.Model):
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

    class Meta:
        verbose_name = "DDT"
        verbose_name_plural = "DDT"
        ordering = ["-anno", "-data", "-id"]
        constraints = [models.UniqueConstraint(fields=["numero", "anno"], name="ddt_numero_anno_unico")]

    def __str__(self) -> str:
        return f"DDT {self.numero}/{self.anno}"


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
