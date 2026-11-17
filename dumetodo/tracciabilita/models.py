"""Tracciabilità (batterie, cavi, tracce generiche, lotto/busta) e qualità (controlli, reclami, test display).

Differenze principali rispetto a Metodo:
- il tipo di elemento tracciato (batteria, cavo, generico) è un campo esplicito di ``Elemento``:
  in Metodo era espresso collegando lo ``Stored_Item`` a righe d'ordine finte, che sporcavano le righe;
- ``Elemento`` sostituisce la coppia ``Plan.Coding`` + ``Stored_Item`` (un record per elemento fisico,
  non uno per evento); il codice è UNICO a livello di database;
- ogni dato legato a un ordine ha una ForeignKey verso ``Ordine`` (in Metodo: stringhe, regex e
  ``contains`` sul solo numero, che mescolavano ordini di serie o anni diversi);
- i banchi di prova sono ``Postazione`` (ex ``Plan.Asset``) e si autenticano con un token segreto;
- i controlli qualità hanno campi numerici veri e un risultato di presenza separato; i controlli
  "distinta" e "disegno" sono controlli come gli altri, riconosciuti dal ``codice`` e mai dalla chiave
  primaria;
- le date inserite dall'utente non vengono più sovrascritte (``auto_now`` su reclami e tracce).
"""

from __future__ import annotations

import uuid
from pathlib import PurePath

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.urls import reverse
from simple_history.models import HistoricalRecords

from dumetodo.core.models import ModelloTemporale


def _percorso(cartella: str, nome: str) -> str:
    """Nome di file casuale (mai quello scelto dall'utente) dentro una cartella non pubblica."""
    estensione = PurePath(nome).suffix.lower()[:10]
    return f"{cartella}/{uuid.uuid4().hex}{estensione}"


# =========================================================================== postazioni
class Postazione(ModelloTemporale):
    """Banco di prova o postazione di lavoro (ex ``Plan.Asset``).

    Le postazioni automatiche (es. la taglierina dei cavi) inviano le tracce all'API autenticandosi
    con un token: nel database se ne conserva solo l'impronta SHA-256.
    """

    codice = models.CharField("codice", max_length=30, unique=True, help_text="Es. GKAST-107 (ex codice asset).")
    nome = models.CharField("nome", max_length=100)
    reparto = models.ForeignKey(
        "core.Reparto", on_delete=models.PROTECT, null=True, blank=True, related_name="postazioni"
    )
    attiva = models.BooleanField("attiva", default=True)
    token_hash = models.CharField("impronta del token", max_length=64, blank=True, default="", editable=False)
    token_generato_il = models.DateTimeField("token generato il", null=True, blank=True, editable=False)

    history = HistoricalRecords(excluded_fields=["token_hash"])

    class Meta:
        verbose_name = "postazione"
        verbose_name_plural = "postazioni"
        ordering = ["codice"]
        constraints = [
            models.UniqueConstraint(fields=["token_hash"], condition=~Q(token_hash=""), name="postazione_token_unico")
        ]

    def __str__(self) -> str:
        return f"{self.codice} – {self.nome}" if self.nome else self.codice

    @property
    def ha_token(self) -> bool:
        return bool(self.token_hash)


# =========================================================================== elementi e tracce
class TipoElemento(models.TextChoices):
    BATTERIA = "BAT", "Batteria"
    CAVO = "CAV", "Cavo"
    GENERICO = "GEN", "Generico"


class Elemento(ModelloTemporale):
    """Elemento fisico tracciato, identificato da un codice univoco (ex ``Coding`` + ``Stored_Item``)."""

    codice = models.CharField("codice", max_length=50, unique=True)
    tipo = models.CharField("tipo", max_length=3, choices=TipoElemento.choices, default=TipoElemento.GENERICO)
    lotto = models.CharField("lotto", max_length=100, blank=True, default="", help_text="Es. lotto del cavo.")
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

    INGRESSO = "IN"
    RICARICA = "RC"
    LOTTO = "LOTTO"
    BUSTA = "BUSTA"
    INTESTATO = "INTESTATO"

    RISERVATE = frozenset({INGRESSO, RICARICA, LOTTO, BUSTA, INTESTATO})
    ETICHETTE = {INGRESSO: "Primo ingresso", RICARICA: "Ricarica", INTESTATO: "Intestato"}


class Traccia(models.Model):
    """Evento di tracciabilità: "l'elemento X / l'ordine Y aveva ``chiave`` = ``valore`` al momento ``data``"."""

    elemento = models.ForeignKey(Elemento, on_delete=models.PROTECT, null=True, blank=True, related_name="tracce")
    ordine = models.ForeignKey("ordini.Ordine", on_delete=models.PROTECT, null=True, blank=True, related_name="tracce")
    data = models.DateTimeField("data evento")
    chiave = models.CharField("chiave", max_length=50)
    valore = models.CharField("valore", max_length=100, blank=True, default="")
    reparto = models.ForeignKey("core.Reparto", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    postazione = models.ForeignKey(Postazione, on_delete=models.PROTECT, null=True, blank=True, related_name="tracce")
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
            # Un banco che reinvia lo stesso dato non crea doppioni (in Metodo: controllo non atomico,
            # più un job O(n²) di pulizia).
            models.UniqueConstraint(
                fields=["postazione", "ordine", "data", "chiave", "valore"],
                condition=Q(postazione__isnull=False),
                name="traccia_postazione_unica",
            ),
            models.UniqueConstraint(fields=["elemento"], condition=Q(chiave="IN"), name="traccia_ingresso_unico"),
            models.UniqueConstraint(
                fields=["elemento"], condition=Q(chiave="INTESTATO"), name="traccia_intestatura_unica"
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
        if self.elemento_id and self.elemento.tipo == TipoElemento.BATTERIA:
            return f"{codice},{self.chiave},{data}"
        return f"{codice},{self.chiave},{self.valore},{data}"


class StatoWorkOrder(models.TextChoices):
    APERTO = "OPN", "Aperto"
    CHIUSO = "CLS", "Chiuso"


class WorkOrderBatteria(models.Model):
    """Richiesta di ricarica di una batteria ferma da troppo tempo (ex ``WorkOrderTraccia``)."""

    elemento = models.ForeignKey(Elemento, on_delete=models.PROTECT, related_name="work_order")
    ultima_traccia = models.ForeignKey(Traccia, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    stato = models.CharField("stato", max_length=3, choices=StatoWorkOrder.choices, default=StatoWorkOrder.APERTO)
    aperto_il = models.DateTimeField("aperto il", auto_now_add=True)
    chiuso_il = models.DateTimeField("chiuso il", null=True, blank=True)
    chiuso_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "work order batteria"
        verbose_name_plural = "work order batterie"
        ordering = ["stato", "-aperto_il"]
        constraints = [
            models.UniqueConstraint(fields=["elemento"], condition=Q(stato="OPN"), name="workorder_uno_aperto")
        ]

    def __str__(self) -> str:
        return f"Work order {self.elemento} ({self.get_stato_display()})"


# =========================================================================== file per ordine
def percorso_file_ordine(instance: FileOrdine, nome: str) -> str:
    return _percorso(f"ordini/{instance.ordine_id}", nome)


class FileOrdine(models.Model):
    """Foto o documento allegato a un ordine. Si scarica solo dalla vista con controllo dei permessi."""

    ordine = models.ForeignKey("ordini.Ordine", on_delete=models.PROTECT, related_name="file_allegati")
    file = models.FileField("file", upload_to=percorso_file_ordine, max_length=255)
    nome_originale = models.CharField("nome originale", max_length=255)
    tipo_contenuto = models.CharField("tipo", max_length=100)
    dimensione = models.PositiveIntegerField("dimensione (byte)")
    caricato_il = models.DateTimeField("caricato il", auto_now_add=True)
    caricato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "file dell'ordine"
        verbose_name_plural = "file degli ordini"
        ordering = ["-caricato_il", "-id"]

    def __str__(self) -> str:
        return f"{self.ordine}: {self.nome_originale}"

    def get_absolute_url(self) -> str:
        return reverse("tracciabilita:file", args=[self.pk])

    @property
    def is_immagine(self) -> bool:
        return self.tipo_contenuto.startswith("image/")


# =========================================================================== controllo qualità
class TipoControllo(models.TextChoices):
    QUANTITA = "QTA", "Campione / KO"
    PRESENZA = "PRE", "Presente / assente"


class Controllo(ModelloTemporale):
    """Singola verifica (ex ``Check``). Es. "visivo" (campione/KO) o "distinta" (presente/assente)."""

    codice = models.SlugField("codice", max_length=50, unique=True)
    nome = models.CharField("nome", max_length=100)
    descrizione = models.CharField("descrizione", max_length=200, blank=True, default="")
    tipo = models.CharField("tipo", max_length=3, choices=TipoControllo.choices, default=TipoControllo.QUANTITA)
    presente_predefinito = models.BooleanField(
        "spuntato di default", default=False, help_text="Solo per i controlli di presenza."
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "controllo"
        verbose_name_plural = "controlli"
        ordering = ["tipo", "nome"]

    def __str__(self) -> str:
        return self.nome


class TipoTest(models.TextChoices):
    CAMPIONE = "CMP", "A campione"
    TOTALE = "100", "100%"
    MISURA = "MIS", "Misura"


class Test(ModelloTemporale):
    """Modello di controllo qualità: l'insieme dei controlli da eseguire."""

    codice = models.SlugField("codice", max_length=50, unique=True)
    nome = models.CharField("nome", max_length=100)
    descrizione = models.CharField("descrizione", max_length=200, blank=True, default="")
    tipo = models.CharField("tipo", max_length=3, choices=TipoTest.choices, default=TipoTest.CAMPIONE)
    controlli = models.ManyToManyField(Controllo, related_name="test", verbose_name="controlli")
    attivo = models.BooleanField("attivo", default=True)

    history = HistoricalRecords(m2m_fields=[controlli])

    class Meta:
        verbose_name = "test qualità"
        verbose_name_plural = "test qualità"
        ordering = ["nome"]

    def __str__(self) -> str:
        return self.nome


class ControlloQualita(models.Model):
    """Esecuzione di un test su un ordine (o su materiale a stock)."""

    ordine = models.ForeignKey(
        "ordini.Ordine", on_delete=models.PROTECT, null=True, blank=True, related_name="controlli_qualita"
    )
    riferimento = models.CharField(
        "stock / riferimento", max_length=50, blank=True, default="", help_text="Quando non si tratta di un ordine."
    )
    articolo = models.CharField("codice prodotto", max_length=50)
    qta_in_ordine = models.DecimalField("quantità in ordine", max_digits=14, decimal_places=3, null=True, blank=True)
    test = models.ForeignKey(Test, on_delete=models.PROTECT, related_name="esecuzioni")
    data = models.DateField("data")
    eseguito_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    creato_il = models.DateTimeField("inserito il", auto_now_add=True)

    class Meta:
        verbose_name = "controllo qualità"
        verbose_name_plural = "controlli qualità"
        ordering = ["-data", "-id"]
        indexes = [models.Index(fields=["data"], name="cq_data_idx")]
        constraints = [
            models.CheckConstraint(
                condition=Q(ordine__isnull=False) | ~Q(riferimento=""), name="cq_ordine_o_riferimento"
            ),
            models.CheckConstraint(
                condition=Q(qta_in_ordine__isnull=True) | Q(qta_in_ordine__gte=0), name="cq_qta_non_negativa"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.test} su {self.ordine or self.riferimento} ({self.data:%d/%m/%Y})"

    @property
    def soggetto(self) -> str:
        return self.ordine.codice_breve if self.ordine_id else self.riferimento


class EsitoControllo(models.Model):
    controllo_qualita = models.ForeignKey(ControlloQualita, on_delete=models.CASCADE, related_name="esiti")
    controllo = models.ForeignKey(Controllo, on_delete=models.PROTECT, related_name="esiti")
    campione = models.PositiveIntegerField("campione", null=True, blank=True)
    ko = models.PositiveIntegerField("KO", null=True, blank=True)
    presente = models.BooleanField("presente", null=True, blank=True)

    class Meta:
        verbose_name = "esito controllo"
        verbose_name_plural = "esiti controllo"
        ordering = ["controllo__tipo", "controllo__nome"]
        constraints = [
            models.UniqueConstraint(fields=["controllo_qualita", "controllo"], name="esito_unico_per_controllo"),
            models.CheckConstraint(
                condition=(Q(campione__isnull=False) & Q(ko__isnull=False) & Q(presente__isnull=True))
                | (Q(campione__isnull=True) & Q(ko__isnull=True) & Q(presente__isnull=False)),
                name="esito_quantita_o_presenza",
            ),
            models.CheckConstraint(
                condition=Q(ko__isnull=True) | Q(ko__lte=F("campione")), name="esito_ko_non_oltre_campione"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.controllo}: {self.risultato}"

    @property
    def risultato(self) -> str:
        if self.presente is not None:
            return "Presente" if self.presente else "Assente"
        return f"{self.ko} KO su {self.campione}"


# =========================================================================== reclami
def percorso_file_reclamo(instance: Reclamo, nome: str) -> str:
    return _percorso("reclami", nome)


class Reclamo(models.Model):
    """Reclamo del cliente / non conformità."""

    codice = models.CharField("identificativo", max_length=50, db_index=True)
    ordine = models.ForeignKey("ordini.Ordine", on_delete=models.PROTECT, null=True, blank=True, related_name="reclami")
    riferimento_ordine = models.CharField(
        "riferimento ordine (testo)",
        max_length=50,
        blank=True,
        default="",
        help_text="Solo per i reclami importati da Metodo il cui ordine non è stato trovato.",
    )
    articolo = models.CharField("codice prodotto", max_length=50)
    reparto = models.ForeignKey("core.Reparto", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    data = models.DateField("data")  # in Metodo era auto_now: la data scelta dall'utente andava persa
    note = models.TextField("note", blank=True, default="")
    file = models.FileField("allegato", upload_to=percorso_file_reclamo, max_length=255, blank=True)
    file_nome_originale = models.CharField(max_length=255, blank=True, default="")
    file_tipo = models.CharField(max_length=100, blank=True, default="")
    creato_il = models.DateTimeField("inserito il", auto_now_add=True)
    creato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "reclamo"
        verbose_name_plural = "reclami"
        ordering = ["-data", "-id"]

    def __str__(self) -> str:
        return f"Reclamo {self.codice}"

    @property
    def ordine_testo(self) -> str:
        return self.ordine.codice_breve if self.ordine_id else self.riferimento_ordine


# =========================================================================== test display
class ProdottoDisplay(models.TextChoices):
    RAFFAELLO28 = "RAFFAELLO28", "Raffaello2.8"


class EsitoDisplay(models.TextChoices):
    POSITIVO = "POS", "Positivo"
    NEGATIVO = "NEG", "Negativo"


class TestDisplay(models.Model):
    prodotto = models.CharField(
        "prodotto", max_length=50, choices=ProdottoDisplay.choices, default=ProdottoDisplay.RAFFAELLO28
    )
    sito = models.CharField("sito", max_length=100)
    articolo = models.CharField("articolo", max_length=100)
    esito = models.CharField("esito", max_length=3, choices=EsitoDisplay.choices)
    fw = models.CharField("FW", max_length=50, blank=True, default="")
    lotto = models.CharField("lotto", max_length=50, blank=True, default="")
    data = models.DateField("data")
    note = models.TextField("note", blank=True, default="")
    creato_il = models.DateTimeField("inserito il", auto_now_add=True)
    utente = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        verbose_name = "test display"
        verbose_name_plural = "test display"
        ordering = ["-data", "-creato_il", "-id"]
        indexes = [models.Index(fields=["data"], name="testdisplay_data_idx")]

    def __str__(self) -> str:
        return f"{self.sito} – {self.articolo}: {self.get_esito_display()}"
