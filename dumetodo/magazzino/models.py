"""Magazzino: anagrafica componenti, inventario, richieste materiali, distinte base, DDT in ingresso.

Differenze principali rispetto a Metodo (vedi docs/analisi-metodo/03-magazzino.md):
- la variante "nessuna" è sempre la stringa vuota, mai NULL: i vincoli di unicità funzionano
  davvero anche in PostgreSQL (in Metodo ``(X, NULL)`` poteva esistere più volte);
- i codici sono normalizzati (maiuscolo, senza spazi ai lati) in un unico punto, ``normalizza_codice``;
- una sola richiesta APERTA per (componente, reparto), garantita da un vincolo nel database
  (in Metodo un job a parte cancellava i doppioni);
- una sola distinta ATTIVA per (articolo, variante): una distinta eliminata non blocca più il codice;
- un DDT in ingresso è unico per (fornitore, numero, data);
- quantità e prezzi sono Decimal; le righe di inventario, distinta e DDT puntano al componente
  con una ForeignKey (niente più campi di testo "legacy" paralleli).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Trim, Upper
from django.urls import reverse
from django.utils import timezone
from simple_history.models import HistoricalRecords

from dumetodo.anagrafica.models import UnitaMisura
from dumetodo.core.models import ModelloTemporale


def normalizza_codice(valore: str | None) -> str:
    """Codici componente e varianti: maiuscolo e senza spazi ai lati (come le richieste di Metodo)."""
    return (valore or "").strip().upper()


def codice_completo(codice: str, variante: str) -> str:
    return f"{codice}.{variante}" if variante else codice


# ----------------------------------------------------------------------------------------- componenti
class Componente(ModelloTemporale):
    """Anagrafica componenti (ex ``CodiceComponente``)."""

    codice = models.CharField("codice", max_length=50)
    variante = models.CharField("variante", max_length=20, blank=True, default="")
    descrizione = models.TextField("descrizione", blank=True, default="")
    um = models.CharField("unità di misura", max_length=3, choices=UnitaMisura.choices, default=UnitaMisura.PEZZI)
    prezzo_medio = models.DecimalField("prezzo medio", max_digits=14, decimal_places=4, null=True, blank=True)
    fornitore = models.CharField("fornitore", max_length=100, blank=True, default="")
    note = models.TextField("note", blank=True, default="")
    attivo = models.BooleanField("attivo", default=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "componente"
        verbose_name_plural = "componenti"
        ordering = ["codice", "variante"]
        constraints = [
            models.UniqueConstraint(fields=["codice", "variante"], name="componente_codice_variante_unico"),
            models.CheckConstraint(condition=~Q(codice=""), name="componente_codice_non_vuoto"),
            models.CheckConstraint(
                condition=Q(codice=Upper(Trim("codice")), variante=Upper(Trim("variante"))),
                name="componente_codice_normalizzato",
            ),
            models.CheckConstraint(
                condition=Q(prezzo_medio__isnull=True) | Q(prezzo_medio__gte=0), name="componente_prezzo_non_negativo"
            ),
        ]

    def __str__(self) -> str:
        return codice_completo(self.codice, self.variante)

    @property
    def codice_completo(self) -> str:
        return codice_completo(self.codice, self.variante)

    def save(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.codice, self.variante = normalizza_codice(self.codice), normalizza_codice(self.variante)
        super().save(*args, **kwargs)


# ----------------------------------------------------------------------------------------- inventario
class ConteggioInventario(models.Model):
    """Un conteggio fisico di un componente (registro in sola aggiunta, come in Metodo).

    La "giacenza" di un componente è il suo ULTIMO conteggio: Metodo sommava invece tutti i
    conteggi mai registrati, un numero senza significato se lo stesso pezzo veniva ricontato.
    """

    componente = models.ForeignKey(Componente, on_delete=models.PROTECT, related_name="conteggi")
    qta = models.DecimalField("quantità", max_digits=14, decimal_places=3)
    data = models.DateTimeField("data conteggio", default=timezone.now, db_index=True)
    utente = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        verbose_name = "conteggio di inventario"
        verbose_name_plural = "conteggi di inventario"
        ordering = ["-data", "-id"]
        indexes = [models.Index(fields=["componente", "-data"], name="conteggio_componente_data")]
        constraints = [models.CheckConstraint(condition=Q(qta__gte=0), name="conteggio_qta_non_negativa")]

    def __str__(self) -> str:
        return f"{self.componente} = {self.qta} ({self.data:%d/%m/%Y})"


# ----------------------------------------------------------------------------------------- richieste
class TipoEmailRichiesta(models.TextChoices):
    MANCANTE = "MANCANTE", "Mancante"
    URGENTE = "URGENTE", "Urgente"
    SOLLECITO = "SOLLECITO", "Sollecito"


class RichiestaMateriale(models.Model):
    """Richiesta di un materiale da parte di un reparto al magazzino del Cliente.

    Il codice resta testuale (come in Metodo): si può chiedere anche un codice non ancora in
    anagrafica, che viene segnalato in elenco. Codice e variante sono normalizzati.
    """

    codice = models.CharField("codice", max_length=50)
    variante = models.CharField("variante", max_length=20, blank=True, default="")
    reparto = models.ForeignKey("core.Reparto", on_delete=models.PROTECT, related_name="richieste_materiali")
    data_richiesta = models.DateTimeField("ultima richiesta", default=timezone.now)
    numero_richieste = models.PositiveIntegerField("numero richieste", default=1)
    urgente = models.BooleanField("urgente", default=False)
    mail_mancante = models.BooleanField("comunicato mancante", default=False)
    mail_urgente = models.BooleanField("comunicato urgente", default=False)
    mail_sollecito = models.BooleanField("sollecitato", default=False)
    ultima_email = models.ForeignKey(
        "comunicazioni.EmailInUscita", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    richiesta_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    ricevuta = models.BooleanField("materiale ricevuto", default=False)
    ricevuta_il = models.DateTimeField("ricevuto il", null=True, blank=True)
    ricevuta_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    ricevuta_con = models.ForeignKey(
        "magazzino.MaterialeIngresso",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="richieste_chiuse",
        verbose_name="chiusa dalla riga DDT",
        help_text="Riga del DDT in ingresso che ha chiuso la richiesta.",
    )
    creata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "richiesta materiale"
        verbose_name_plural = "richieste materiali"
        ordering = ["-data_richiesta", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["codice", "variante", "reparto"],
                condition=Q(ricevuta=False),
                name="richiesta_aperta_unica_per_reparto",
            ),
            models.CheckConstraint(condition=~Q(codice=""), name="richiesta_codice_non_vuoto"),
            models.CheckConstraint(
                condition=Q(codice=Upper(Trim("codice")), variante=Upper(Trim("variante"))),
                name="richiesta_codice_normalizzato",
            ),
            models.CheckConstraint(
                condition=Q(ricevuta=False) | Q(ricevuta_il__isnull=False), name="richiesta_ricevuta_con_data"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.codice_completo} ({self.reparto})"

    @property
    def codice_completo(self) -> str:
        return codice_completo(self.codice, self.variante)


# ----------------------------------------------------------------------------------------- distinte base
class DistintaQuerySet(models.QuerySet["DistintaBase"]):
    def attive(self) -> DistintaQuerySet:
        return self.filter(eliminata_il__isnull=True)


class DistintaBase(ModelloTemporale):
    """Distinta base a un livello di un articolo finito (``anagrafica.Articolo``)."""

    articolo = models.ForeignKey("anagrafica.Articolo", on_delete=models.PROTECT, related_name="distinte")
    variante = models.CharField("variante", max_length=20, blank=True, default="")
    descrizione = models.TextField("descrizione", blank=True, default="")
    note = models.TextField("note", blank=True, default="")
    creata_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    modificata_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    eliminata_il = models.DateTimeField("eliminata il", null=True, blank=True)
    eliminata_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = DistintaQuerySet.as_manager()
    history = HistoricalRecords()

    class Meta:
        verbose_name = "distinta base"
        verbose_name_plural = "distinte base"
        ordering = ["-creato_il", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["articolo", "variante"],
                condition=Q(eliminata_il__isnull=True),
                name="distinta_attiva_unica",
            )
        ]

    def __str__(self) -> str:
        return f"Distinta {codice_completo(self.articolo.codice, self.variante)}"

    def get_absolute_url(self) -> str:
        return reverse("magazzino:distinta", args=[self.pk])

    @property
    def codice_completo(self) -> str:
        return codice_completo(self.articolo.codice, self.variante)


class ComponenteDistinta(models.Model):
    distinta = models.ForeignKey(DistintaBase, on_delete=models.CASCADE, related_name="componenti")
    # PROTECT: in Metodo eliminare un componente dall'anagrafica lo toglieva in silenzio da ogni distinta.
    componente = models.ForeignKey(Componente, on_delete=models.PROTECT, related_name="in_distinte")
    quantita = models.DecimalField("quantità", max_digits=14, decimal_places=4)
    um = models.CharField("unità di misura", max_length=3, choices=UnitaMisura.choices, default=UnitaMisura.PEZZI)
    posizione = models.PositiveIntegerField("posizione", default=1)
    note = models.TextField("note", blank=True, default="")

    history = HistoricalRecords()

    class Meta:
        verbose_name = "componente della distinta"
        verbose_name_plural = "componenti della distinta"
        ordering = ["distinta", "posizione", "id"]
        constraints = [
            models.UniqueConstraint(fields=["distinta", "componente"], name="distinta_componente_unico"),
            models.CheckConstraint(condition=Q(quantita__gt=0), name="distinta_quantita_positiva"),
        ]

    def __str__(self) -> str:
        return f"{self.componente} × {self.quantita}"


# ----------------------------------------------------------------------------------------- DDT in ingresso
class OrigineDDT(models.TextChoices):
    MANUALE = "MAN", "Inserimento manuale"
    FILE = "CSV", "File CSV/TXT"
    XML = "XML", "File XML"
    METODO = "MET", "Importato da Metodo"


class DDTIngresso(ModelloTemporale):
    numero = models.CharField("numero DDT", max_length=50)
    data = models.DateField("data DDT")
    fornitore = models.CharField("fornitore", max_length=100)
    origine = models.CharField("origine", max_length=3, choices=OrigineDDT.choices, default=OrigineDDT.MANUALE)
    creato_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        verbose_name = "DDT in ingresso"
        verbose_name_plural = "DDT in ingresso"
        ordering = ["-data", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["fornitore", "numero", "data"], name="ddt_ingresso_unico"),
            models.CheckConstraint(condition=~Q(numero=""), name="ddt_ingresso_numero_non_vuoto"),
        ]

    def __str__(self) -> str:
        return f"DDT {self.numero} del {self.data:%d/%m/%Y} ({self.fornitore})"

    def get_absolute_url(self) -> str:
        return reverse("magazzino:ddt", args=[self.pk])


class MaterialeIngresso(models.Model):
    ddt = models.ForeignKey(DDTIngresso, on_delete=models.CASCADE, related_name="materiali")
    componente = models.ForeignKey(Componente, on_delete=models.PROTECT, related_name="ingressi")
    # Posizione della riga nel documento importato: identifica la riga in caso di reimportazione.
    # In Metodo la chiave era (DDT, codice, data) e due righe con lo stesso codice diventavano una.
    riga_documento = models.PositiveIntegerField("riga nel documento", null=True, blank=True)
    descrizione = models.CharField("descrizione", max_length=200, blank=True, default="")
    quantita = models.DecimalField("quantità", max_digits=14, decimal_places=3)
    unita_misura = models.CharField("unità di misura", max_length=10, blank=True, default="EA")
    prezzo_unitario = models.DecimalField("prezzo unitario", max_digits=14, decimal_places=4, null=True, blank=True)
    data_ingresso = models.DateField("data ingresso")
    verificato = models.BooleanField("verificato", default=False)
    note = models.TextField("note", blank=True, default="")
    creato_il = models.DateTimeField(auto_now_add=True)
    creato_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        verbose_name = "materiale in ingresso"
        verbose_name_plural = "materiali in ingresso"
        ordering = ["ddt", "riga_documento", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["ddt", "riga_documento"],
                condition=Q(riga_documento__isnull=False),
                name="materiale_ingresso_riga_unica",
            ),
            models.CheckConstraint(condition=Q(quantita__gt=0), name="materiale_ingresso_quantita_positiva"),
            models.CheckConstraint(
                condition=Q(prezzo_unitario__isnull=True) | Q(prezzo_unitario__gte=0),
                name="materiale_ingresso_prezzo_non_negativo",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.componente} × {self.quantita} ({self.ddt})"
