"""Comunicazione con il cliente tramite note sull'ordine.

Problemi risolti rispetto a Metodo:
- la nota veniva salvata e POI l'email inviata in modo sincrono: se l'SMTP falliva l'utente
  vedeva un errore 500 ma la nota restava salvata (e riprovando si duplicava);
- il testo finiva nell'email HTML senza escape (iniezione di HTML);
- cancellare un utente cancellava le sue note (CASCADE); la data era quella di modifica;
- nessuna distinzione tra nota interna e nota per il cliente, nessun "letto/non letto";
- l'eliminazione era una GET senza controllo dell'autore.

Ora: nota + email in coda vengono salvate nella STESSA transazione (pattern "outbox");
un worker invia l'email con tentativi ripetuti. L'utente non aspetta l'SMTP e nulla si perde.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class Lato(models.TextChoices):
    FORNITORE = "FOR", "Fornitore"
    CLIENTE = "CLI", "Cliente"


class Visibilita(models.TextChoices):
    CONDIVISA = "CON", "Condivisa con il cliente"
    INTERNA = "INT", "Solo interna (Fornitore)"


class NotaQuerySet(models.QuerySet["Nota"]):
    def visibili(self) -> NotaQuerySet:
        return self.filter(eliminata_il__isnull=True)

    def per_utente(self, fornitore: bool) -> NotaQuerySet:
        qs = self.visibili()
        return qs if fornitore else qs.filter(visibilita=Visibilita.CONDIVISA)


class Nota(models.Model):
    ordine = models.ForeignKey("ordini.Ordine", on_delete=models.CASCADE, related_name="note")
    autore = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    autore_nome = models.CharField(
        "autore", max_length=150, help_text="Copia del nome: resta anche se l'utente viene rimosso."
    )
    lato = models.CharField(max_length=4, choices=Lato.choices)
    visibilita = models.CharField(max_length=3, choices=Visibilita.choices, default=Visibilita.CONDIVISA)
    testo = models.TextField("testo")
    creata_il = models.DateTimeField(default=timezone.now, db_index=True)
    eliminata_il = models.DateTimeField(null=True, blank=True)
    eliminata_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = NotaQuerySet.as_manager()

    class Meta:
        verbose_name = "nota"
        verbose_name_plural = "note"
        ordering = ["creata_il", "id"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(testo=""), name="nota_testo_non_vuoto"),
            # Il cliente non può scrivere note interne.
            models.CheckConstraint(
                condition=~(models.Q(lato="CLI") & models.Q(visibilita="INT")), name="nota_cliente_sempre_condivisa"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.ordine}: {self.testo[:30]}"

    @property
    def eliminata(self) -> bool:
        return self.eliminata_il is not None


class LetturaNota(models.Model):
    nota = models.ForeignKey(Nota, on_delete=models.CASCADE, related_name="letture")
    utente = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    letta_il = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["nota", "utente"], name="lettura_unica")]

    def __str__(self) -> str:
        return f"{self.utente_id} ha letto {self.nota_id}"


class StatoEmail(models.TextChoices):
    IN_CODA = "CODA", "In coda"
    INVIATA = "INV", "Inviata"
    ERRORE = "ERR", "Errore (nuovo tentativo previsto)"
    FALLITA = "FAL", "Fallita definitivamente"


class EmailInUscita(models.Model):
    """Coda delle email (outbox). Sostituisce l'invio sincrono e il log ``SentEmail`` di Metodo."""

    oggetto = models.CharField(max_length=255)
    corpo_testo = models.TextField()
    corpo_html = models.TextField(blank=True, default="")
    mittente = models.EmailField()
    destinatari = models.JSONField(default=list)
    cc = models.JSONField(default=list, blank=True)
    rispondi_a = models.JSONField(default=list, blank=True)
    stato = models.CharField(max_length=4, choices=StatoEmail.choices, default=StatoEmail.IN_CODA, db_index=True)
    tentativi = models.PositiveSmallIntegerField(default=0)
    ultimo_errore = models.TextField(blank=True, default="")
    prossimo_tentativo_il = models.DateTimeField(default=timezone.now, db_index=True)
    creata_il = models.DateTimeField(auto_now_add=True)
    inviata_il = models.DateTimeField(null=True, blank=True)
    # Riferimento facoltativo all'oggetto che ha generato l'email (per la consultazione).
    riferimento = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        verbose_name = "email in uscita"
        verbose_name_plural = "email in uscita"
        ordering = ["-creata_il"]

    def __str__(self) -> str:
        return f"[{self.get_stato_display()}] {self.oggetto}"


class AllegatoEmail(models.Model):
    email = models.ForeignKey(EmailInUscita, on_delete=models.CASCADE, related_name="allegati")
    nome = models.CharField(max_length=255)
    contenuto = models.BinaryField()
    tipo_mime = models.CharField(max_length=100, default="application/octet-stream")

    class Meta:
        verbose_name = "allegato email"
        verbose_name_plural = "allegati email"

    def __str__(self) -> str:
        return self.nome
