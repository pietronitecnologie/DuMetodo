"""Tracciatura configurabile: modelli con campi tipizzati, collegati a ordini, DDT, codici, lotti.

* ``Modello``: un tipo di tracciatura (es. «Collaudo pulsantiera»): i suoi campi, a cosa si collega e a quali
  codici articolo / finiti si applica.
* ``Campo``: un dato da raccogliere, con il suo tipo (testo, intero, decimale con limiti, sì/no, scelta, data,
  esito, foto, documento). I campi già usati non si cancellano né cambiano tipo: si disattivano.
* ``Tracciatura``: una registrazione, con i collegamenti e l'esito calcolato.
* ``Valore``: il valore di un campo in una tracciatura, in una colonna del suo tipo (filtrabile e ordinabile).
"""

from __future__ import annotations

import uuid
from pathlib import PurePath

from django.conf import settings
from django.db import models
from django.urls import reverse
from simple_history.models import HistoricalRecords

from metodo.core.models import ModelloTemporale


class Categoria(models.TextChoices):
    QUALITA = "QUA", "Controllo qualità"
    LOTTO = "LOT", "Controllo di lotto"
    OPERAZIONE = "OPE", "Operazione"
    DOCUMENTO = "DOC", "Documento"
    ALTRO = "ALT", "Altro"


class Collegamento(models.TextChoices):
    ORDINE = "ordine", "Ordine"
    DDT = "ddt", "DDT"
    COMPONENTE = "componente", "Codice articolo (componente)"
    ARTICOLO = "articolo", "Codice finito"
    LOTTO = "lotto", "Lotto"
    RIFERIMENTO = "riferimento", "Riferimento libero (es. numero di serie)"


class Uso(models.TextChoices):
    NO = "", "—"
    FACOLTATIVO = "F", "Facoltativo"
    OBBLIGATORIO = "O", "Obbligatorio"


class Modello(ModelloTemporale):
    nome = models.CharField("nome", max_length=80, unique=True)
    categoria = models.CharField("categoria", max_length=3, choices=Categoria.choices, default=Categoria.QUALITA)
    descrizione = models.TextField("istruzioni per l'operatore", blank=True, default="")
    # Collegamenti: "" = non previsto, F = facoltativo, O = obbligatorio.
    uso_ordine = models.CharField(max_length=1, choices=Uso.choices, blank=True, default=Uso.FACOLTATIVO)
    uso_ddt = models.CharField(max_length=1, choices=Uso.choices, blank=True, default="")
    uso_componente = models.CharField(max_length=1, choices=Uso.choices, blank=True, default="")
    uso_articolo = models.CharField(max_length=1, choices=Uso.choices, blank=True, default=Uso.FACOLTATIVO)
    uso_lotto = models.CharField(max_length=1, choices=Uso.choices, blank=True, default="")
    uso_riferimento = models.CharField(max_length=1, choices=Uso.choices, blank=True, default="")
    codici = models.TextField(
        "si applica ai codici",
        blank=True,
        default="",
        help_text="Uno per riga, anche con * (es. 18090*). Vuoto = tutti. Vale per codici articolo e codici finiti.",
    )
    attivo = models.BooleanField("attivo", default=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "modello di tracciatura"
        verbose_name_plural = "modelli di tracciatura"
        ordering = ["nome"]

    def __str__(self) -> str:
        return self.nome

    def get_absolute_url(self) -> str:
        return reverse("tracciatura:modello", args=[self.pk])

    def uso(self, collegamento: str) -> str:
        return getattr(self, f"uso_{collegamento}")

    @property
    def collegamenti(self) -> list[tuple[str, str, str]]:
        """(chiave, etichetta, uso) dei collegamenti previsti."""
        return [(c.value, c.label, self.uso(c.value)) for c in Collegamento if self.uso(c.value)]


class TipoCampo(models.TextChoices):
    TESTO = "TXT", "Testo breve"
    TESTO_LUNGO = "TXL", "Testo lungo (descrizione)"
    INTERO = "INT", "Numero intero"
    DECIMALE = "DEC", "Numero decimale"
    SI_NO = "BOO", "Sì / No"
    SCELTA = "SCE", "Scelta da un elenco"
    DATA = "DAT", "Data"
    DATA_ORA = "DTO", "Data e ora"
    ESITO = "ESI", "Esito OK / KO"
    FOTO = "FOT", "Fotografia"
    DOCUMENTO = "DOC", "Documento (PDF o immagine)"


TIPI_FILE = (TipoCampo.FOTO, TipoCampo.DOCUMENTO)
TIPI_NUMERO = (TipoCampo.INTERO, TipoCampo.DECIMALE)


class Campo(models.Model):
    modello = models.ForeignKey(Modello, on_delete=models.CASCADE, related_name="campi")
    etichetta = models.CharField("etichetta", max_length=80)
    tipo = models.CharField("tipo", max_length=3, choices=TipoCampo.choices)
    obbligatorio = models.BooleanField("obbligatorio", default=False)
    ordine = models.PositiveSmallIntegerField(default=0)
    aiuto = models.CharField("istruzioni", max_length=200, blank=True, default="")
    unita = models.CharField("unità di misura", max_length=15, blank=True, default="")
    minimo = models.DecimalField("minimo accettabile", max_digits=16, decimal_places=6, null=True, blank=True)
    massimo = models.DecimalField("massimo accettabile", max_digits=16, decimal_places=6, null=True, blank=True)
    scelte = models.TextField("scelte", blank=True, default="", help_text="Una per riga.")
    scelte_non_conformi = models.TextField(
        "scelte non conformi",
        blank=True,
        default="",
        help_text="Una per riga: se scelte, la tracciatura è non conforme.",
    )
    atteso_si = models.BooleanField(
        "risposta attesa «Sì»", null=True, blank=True, help_text="Per Sì/No: la risposta diversa rende non conforme."
    )
    attivo = models.BooleanField("attivo", default=True)

    class Meta:
        verbose_name = "campo"
        verbose_name_plural = "campi"
        ordering = ["ordine", "id"]

    def __str__(self) -> str:
        return self.etichetta

    @property
    def elenco_scelte(self) -> list[str]:
        return [s.strip() for s in self.scelte.splitlines() if s.strip()]

    @property
    def elenco_non_conformi(self) -> list[str]:
        return [s.strip() for s in self.scelte_non_conformi.splitlines() if s.strip()]

    @property
    def ha_controllo(self) -> bool:
        """Il campo contribuisce all'esito?"""
        return (
            self.tipo == TipoCampo.ESITO
            or (self.tipo in TIPI_NUMERO and (self.minimo is not None or self.massimo is not None))
            or (self.tipo == TipoCampo.SCELTA and bool(self.elenco_non_conformi))
            or (self.tipo == TipoCampo.SI_NO and self.atteso_si is not None)
        )


class Esito(models.TextChoices):
    CONFORME = "OK", "Conforme"
    NON_CONFORME = "KO", "Non conforme"
    SENZA = "", "Senza esito"


class Tracciatura(models.Model):
    modello = models.ForeignKey(Modello, on_delete=models.PROTECT, related_name="tracciature")
    momento = models.DateTimeField(db_index=True)
    operatore = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    ordine = models.ForeignKey("ordini.Ordine", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    ddt = models.ForeignKey("consegne.DDT", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    componente = models.CharField("codice articolo", max_length=80, blank=True, default="", db_index=True)
    articolo = models.CharField("codice finito", max_length=80, blank=True, default="", db_index=True)
    lotto = models.CharField("lotto", max_length=80, blank=True, default="", db_index=True)
    riferimento = models.CharField("riferimento", max_length=120, blank=True, default="", db_index=True)
    note = models.TextField("note", blank=True, default="")
    esito = models.CharField(max_length=2, choices=Esito.choices, blank=True, default="", db_index=True)
    annullata_il = models.DateTimeField(null=True, blank=True)
    annullata_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    motivo_annullamento = models.CharField(max_length=200, blank=True, default="")

    history = HistoricalRecords()

    class Meta:
        verbose_name = "tracciatura"
        verbose_name_plural = "tracciature"
        ordering = ["-momento", "-id"]

    def __str__(self) -> str:
        return f"{self.modello} del {self.momento:%d/%m/%Y %H:%M}"

    def get_absolute_url(self) -> str:
        return reverse("tracciatura:tracciatura", args=[self.pk])

    @property
    def annullata(self) -> bool:
        return self.annullata_il is not None


def percorso_file(instance: Valore, nome: str) -> str:
    """Nome casuale in una cartella non pubblica: i file si scaricano solo dalla vista con i permessi."""
    return f"tracciatura/{instance.tracciatura_id}/{uuid.uuid4().hex}{PurePath(nome).suffix.lower()[:10]}"


class Valore(models.Model):
    tracciatura = models.ForeignKey(Tracciatura, on_delete=models.CASCADE, related_name="valori")
    campo = models.ForeignKey(Campo, on_delete=models.PROTECT, related_name="valori")
    testo = models.TextField(blank=True, default="")
    intero = models.BigIntegerField(null=True, blank=True)
    decimale = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    booleano = models.BooleanField(null=True, blank=True)
    data = models.DateField(null=True, blank=True)
    data_ora = models.DateTimeField(null=True, blank=True)
    file = models.FileField(upload_to=percorso_file, max_length=255, blank=True)
    nome_file = models.CharField(max_length=255, blank=True, default="")
    tipo_file = models.CharField(max_length=100, blank=True, default="")
    conforme = models.BooleanField(null=True, blank=True)

    class Meta:
        verbose_name = "valore"
        verbose_name_plural = "valori"
        ordering = ["campo__ordine", "campo_id", "id"]

    def __str__(self) -> str:
        return f"{self.campo}: {self.testo_leggibile}"

    @property
    def testo_leggibile(self) -> str:
        t = self.campo.tipo
        if t == TipoCampo.INTERO:
            return "" if self.intero is None else str(self.intero)
        if t == TipoCampo.DECIMALE:
            if self.decimale is None:
                return ""
            return f"{self.decimale.normalize():f}".replace(".", ",")
        if t == TipoCampo.SI_NO:
            return "" if self.booleano is None else ("Sì" if self.booleano else "No")
        if t == TipoCampo.ESITO:
            return "" if self.booleano is None else ("OK" if self.booleano else "KO")
        if t == TipoCampo.DATA:
            return f"{self.data:%d/%m/%Y}" if self.data else ""
        if t == TipoCampo.DATA_ORA:
            from django.utils import timezone

            return f"{timezone.localtime(self.data_ora):%d/%m/%Y %H:%M}" if self.data_ora else ""
        if t in TIPI_FILE:
            return self.nome_file
        return self.testo
