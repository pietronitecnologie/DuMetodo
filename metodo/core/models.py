from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords


class ModelloTemporale(models.Model):
    creato_il = models.DateTimeField("creato il", auto_now_add=True)
    aggiornato_il = models.DateTimeField("aggiornato il", auto_now=True)

    class Meta:
        abstract = True


class Reparto(ModelloTemporale):
    """Reparto produttivo (ex ``Plan.Location``).

    La soglia settimanale sostituisce le chiavi ``SOGLIA_<AREA>`` della vecchia tabella
    Configuration: è un dato del reparto, non una stringa in una tabella chiave/valore.
    """

    codice = models.CharField("codice", max_length=30, unique=True, help_text="Es. GK01-PLS (ex path code).")
    nome = models.CharField("nome", max_length=80, unique=True)
    soglia_ore_settimanali = models.DecimalField(
        "capacità settimanale (ore)", max_digits=8, decimal_places=2, null=True, blank=True
    )
    attivo = models.BooleanField("attivo", default=True)
    gruppo_report = models.CharField(
        "gruppo nel report di pianificazione",
        max_length=80,
        blank=True,
        default="",
        help_text="Reparti con lo stesso gruppo vengono sommati in un'unica riga nel report Excel/email "
        "(in Metodo: 'Area Quadristica', con 6 nomi di reparto scritti nel codice).",
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "reparto"
        verbose_name_plural = "reparti"
        ordering = ["nome"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(soglia_ore_settimanali__isnull=True) | models.Q(soglia_ore_settimanali__gte=0),
                name="reparto_soglia_non_negativa",
            )
        ]

    def __str__(self) -> str:
        return self.nome


class Funzionalita(models.Model):
    """Modello "contenitore" dei permessi per funzione (nessuna tabella: managed=False).
    I permessi sono dichiarati in ``core/permessi.py`` e sincronizzati a ogni migrate."""

    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = "funzionalità"
        verbose_name_plural = "funzionalità"

    def __str__(self) -> str:
        return "funzionalità"


class ProfiloUtente(models.Model):
    """Dati di Metodo v2 legati all'utente, oltre a quelli standard di Django."""

    utente = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profilo_metodo")
    deve_cambiare_password = models.BooleanField(
        "deve cambiare la password al prossimo accesso",
        default=False,
        help_text="Impostato quando un amministratore genera una password temporanea.",
    )
    note = models.TextField("note", blank=True, default="", help_text="Es. reparto, turno, telefono interno.")
    committenti = models.ManyToManyField(
        "committenti.Committente",
        blank=True,
        related_name="+",
        verbose_name="clienti committenti",
        help_text="Utenti del Cliente: i committenti che vedono. Utenti interni: vuoto = tutti.",
    )

    class Meta:
        verbose_name = "profilo utente"
        verbose_name_plural = "profili utente"

    def __str__(self) -> str:
        return str(self.utente)


class EventoUtente(models.Model):
    """Storico delle modifiche fatte da Utenti e permessi (chi, a chi, cosa). Le password non compaiono mai."""

    class Azione(models.TextChoices):
        CREATO = "CRE", "Utente creato"
        DATI = "DAT", "Dati modificati"
        ATTIVATO = "ATT", "Attivato"
        DISATTIVATO = "DIS", "Disattivato"
        PERMESSI = "PER", "Permessi modificati"
        PROFILO = "PRO", "Profilo applicato"
        PASSWORD = "PWD", "Password reimpostata"
        PASSWORD_TEMPORANEA = "PWT", "Password temporanea generata"
        PASSWORD_PROPRIA = "PWP", "Password cambiata dall'utente"
        SUPERUSER = "SUP", "Superuser modificato"

    quando = models.DateTimeField("quando", auto_now_add=True, db_index=True)
    attore = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    utente = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="eventi_utente")
    azione = models.CharField("azione", max_length=3, choices=Azione.choices)
    dettaglio = models.TextField("dettaglio", blank=True, default="")

    class Meta:
        verbose_name = "evento utente"
        verbose_name_plural = "eventi utenti"
        ordering = ["-quando", "-id"]

    def __str__(self) -> str:
        return f"{self.utente}: {self.get_azione_display()}"
