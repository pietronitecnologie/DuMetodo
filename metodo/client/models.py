import uuid
from pathlib import PurePath

from django.conf import settings
from django.db import models


def percorso_eseguibile(instance: "VersioneClient", nome: str) -> str:
    # Nome casuale: mai quello scelto da chi carica il file.
    return f"client/{uuid.uuid4().hex}{PurePath(nome).suffix.lower()[:5] or '.exe'}"


class VersioneClient(models.Model):
    """Versione del client desktop caricata dall'amministratore: i client la propongono agli utenti."""

    versione = models.CharField("versione", max_length=20, unique=True, help_text="Es. 0.3.0")
    file = models.FileField("eseguibile", upload_to=percorso_eseguibile)
    sha256 = models.CharField("impronta SHA-256", max_length=64, editable=False)
    dimensione = models.PositiveBigIntegerField("dimensione (byte)", editable=False)
    note = models.TextField("novità", blank=True, default="")
    caricata_il = models.DateTimeField("caricata il", auto_now_add=True)
    caricata_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    attiva = models.BooleanField("proposta ai client", default=True)

    class Meta:
        verbose_name = "versione del client"
        verbose_name_plural = "versioni del client"
        ordering = ["-caricata_il"]

    def __str__(self) -> str:
        return f"Client {self.versione}"

    @property
    def numeri(self) -> tuple[int, ...]:
        return numeri_versione(self.versione)


def numeri_versione(testo: str) -> tuple[int, ...]:
    """'0.10.2' -> (0, 10, 2); parti non numeriche = 0 (confronto tra versioni)."""
    risultato = []
    for parte in (testo or "").strip().lstrip("vV").split(".")[:4]:
        cifre = "".join(c for c in parte if c.isdigit())
        risultato.append(int(cifre) if cifre else 0)
    return tuple(risultato)
