"""Il committente predefinito: è il cliente per cui si lavorava finora (nome da Configurazione → Azienda)."""

import re

from django.db import migrations


def crea(apps, schema_editor):
    Committente = apps.get_model("committenti", "Committente")
    if Committente.objects.exists():
        return
    ValoreParametro = apps.get_model("configurazione", "ValoreParametro")
    v = ValoreParametro.objects.filter(chiave="azienda.nome_cliente").first()
    nome = (str(v.valore).strip() if v and v.valore else "") or "Cliente"
    codice = re.sub(r"[^A-Z0-9_-]", "", nome.upper().replace(" ", "-"))[:20] or "CLIENTE"
    Committente.objects.create(codice=codice, nome=nome[:100], predefinito=True)


class Migration(migrations.Migration):
    dependencies = [
        ("committenti", "0001_iniziale"),
        ("configurazione", "0001_initial"),
    ]

    operations = [migrations.RunPython(crea, migrations.RunPython.noop)]
