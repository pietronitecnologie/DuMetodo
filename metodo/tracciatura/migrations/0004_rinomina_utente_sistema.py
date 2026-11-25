"""Rinomina in Metodo v2: l'utente tecnico dei dati storici diventa «metodo-sistema»."""

from django.conf import settings
from django.db import migrations


def rinomina(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    if not User.objects.filter(username="metodo-sistema").exists():
        User.objects.filter(username="dumetodo-sistema").update(username="metodo-sistema")


def indietro(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    User.objects.filter(username="metodo-sistema").update(username="dumetodo-sistema")


class Migration(migrations.Migration):
    dependencies = [
        ("tracciatura", "0003_tracce_in_tracciature"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(rinomina, indietro)]
