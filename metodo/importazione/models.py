"""Tracciamento completo di ogni importazione.

In Metodo i file venivano estratti in una cartella condivisa, importati riga per riga senza
transazione e poi cancellati: in caso di errore restava un ordine a metà e nessuna traccia del
file originale. Ora ogni file è conservato con il suo esito e con i problemi riscontrati, che
un operatore può consultare e segnare come gestiti (ex tabella ``Errori``).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from metodo.committenti.models import predefinito_id


class StatoCasella(models.Model):
    """Cursore della casella IMAP (ex chiave ``ID_EMAIL`` della tabella Configuration).

    Usa gli UID IMAP con UIDVALIDITY: in Metodo il cursore mescolava UID e indici di lista."""

    casella = models.CharField(max_length=255, unique=True)
    uidvalidity = models.BigIntegerField(null=True, blank=True)
    ultimo_uid = models.BigIntegerField(default=0)
    ultima_lettura_il = models.DateTimeField(null=True, blank=True)
    ultimo_errore = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "stato casella"
        verbose_name_plural = "stato caselle"

    def __str__(self) -> str:
        return self.casella


class EsitoMessaggio(models.TextChoices):
    ELABORATO = "OK", "Elaborato"
    SENZA_ALLEGATI = "VUO", "Nessun allegato d'ordine"
    DUPLICATO = "DUP", "Già elaborato (stesso Message-ID)"
    SCARTATO = "SCA", "Scartato"


class MessaggioEmail(models.Model):
    casella = models.CharField(max_length=255)
    uidvalidity = models.BigIntegerField()
    uid = models.BigIntegerField()
    message_id = models.CharField(max_length=500, blank=True, default="", db_index=True)
    mittente = models.CharField(max_length=500, blank=True, default="")
    oggetto = models.CharField(max_length=500, blank=True, default="")
    ricevuto_il = models.DateTimeField(null=True, blank=True)
    elaborato_il = models.DateTimeField(auto_now_add=True)
    esito = models.CharField(max_length=3, choices=EsitoMessaggio.choices)
    dettaglio = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "email ricevuta"
        verbose_name_plural = "email ricevute"
        ordering = ["-elaborato_il"]
        constraints = [
            models.UniqueConstraint(fields=["casella", "uidvalidity", "uid"], name="messaggio_uid_unico"),
        ]

    def __str__(self) -> str:
        return self.oggetto or self.message_id


class OrigineFile(models.TextChoices):
    EMAIL = "MAIL", "Email"
    CARICAMENTO = "UPL", "Caricamento manuale"


class StatoFile(models.TextChoices):
    IN_ATTESA = "ATT", "In attesa"
    IMPORTATO = "OK", "Importato"
    CON_AVVISI = "AVV", "Importato con avvisi"
    CON_ERRORI = "ERR", "Errori (alcuni ordini non importati)"
    NON_VALIDO = "NV", "File non valido"
    DUPLICATO = "DUP", "Duplicato (già importato)"


class FileImportato(models.Model):
    nome = models.CharField(max_length=255)
    origine = models.CharField(max_length=4, choices=OrigineFile.choices)
    # Metodo v3: gli ordini del file sono di questo cliente committente (dalla casella o dal mittente dell'email,
    # oppure scelto da chi carica il file).
    committente = models.ForeignKey(
        "committenti.Committente",
        on_delete=models.PROTECT,
        related_name="file_importati",
        default=predefinito_id,
        verbose_name="cliente committente",
    )
    contenuto = models.BinaryField("contenuto originale")
    sha256 = models.CharField(max_length=64, db_index=True)
    messaggio = models.ForeignKey(MessaggioEmail, on_delete=models.SET_NULL, null=True, blank=True, related_name="file")
    caricato_da = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    stato = models.CharField(max_length=3, choices=StatoFile.choices, default=StatoFile.IN_ATTESA, db_index=True)
    creato_il = models.DateTimeField(auto_now_add=True)
    elaborato_il = models.DateTimeField(null=True, blank=True)

    righe_lette = models.PositiveIntegerField(default=0)
    ordini_creati = models.PositiveIntegerField(default=0)
    ordini_aggiornati = models.PositiveIntegerField(default=0)
    ordini_scartati = models.PositiveIntegerField(default=0)
    righe_create = models.PositiveIntegerField(default=0)
    righe_aggiornate = models.PositiveIntegerField(default=0)
    righe_annullate = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "file importato"
        verbose_name_plural = "file importati"
        ordering = ["-creato_il", "-id"]

    def __str__(self) -> str:
        return self.nome


class Livello(models.TextChoices):
    ERRORE = "ERR", "Errore"
    AVVISO = "AVV", "Avviso"


class ProblemaImportazione(models.Model):
    file = models.ForeignKey(FileImportato, on_delete=models.CASCADE, related_name="problemi")
    livello = models.CharField(max_length=3, choices=Livello.choices)
    riga_file = models.PositiveIntegerField(
        null=True, blank=True, help_text="Numero di riga nel file (1 = intestazione)."
    )
    chiave_ordine = models.CharField(max_length=80, blank=True, default="")
    ordine = models.ForeignKey("ordini.Ordine", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    messaggio = models.TextField()
    gestito = models.BooleanField(default=False, db_index=True)
    gestito_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    gestito_il = models.DateTimeField(null=True, blank=True)
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "problema di importazione"
        verbose_name_plural = "problemi di importazione"
        ordering = ["-creato_il", "id"]

    def __str__(self) -> str:
        return f"[{self.get_livello_display()}] {self.messaggio[:60]}"
