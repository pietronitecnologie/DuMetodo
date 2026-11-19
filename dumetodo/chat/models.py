from django.conf import settings
from django.db import models


class MessaggioChat(models.Model):
    """Messaggio di una chat 1 a 1 tra due utenti (salvato sul server)."""

    mittente = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_inviati")
    destinatario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_ricevuti")
    testo = models.TextField("testo", max_length=2000)
    inviato_il = models.DateTimeField("inviato il", auto_now_add=True, db_index=True)
    letto_il = models.DateTimeField("letto il", null=True, blank=True)

    class Meta:
        verbose_name = "messaggio chat"
        verbose_name_plural = "messaggi chat"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["destinatario", "letto_il"], name="chat_non_letti_idx"),
            models.Index(fields=["mittente", "destinatario", "id"], name="chat_coppia_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.mittente} → {self.destinatario}: {self.testo[:40]}"


class ConversazioneChiusa(models.Model):
    """L'utente ha chiuso la conversazione: sparisce dal suo pannello finché non arriva un nuovo messaggio.
    I messaggi restano salvati e si rivedono riaprendo la conversazione."""

    utente = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    altro = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    ultimo_messaggio = models.PositiveBigIntegerField("chiusa dopo il messaggio", default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["utente", "altro"], name="chat_chiusura_unica")]

    def __str__(self) -> str:
        return f"{self.utente} ha chiuso la chat con {self.altro}"
