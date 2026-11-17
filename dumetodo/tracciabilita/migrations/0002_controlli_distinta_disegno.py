"""Controlli di presenza "distinta" e "disegno".

In Metodo erano i Check con id 8 e 9, scritti nel codice, più due flag sul Test. Ora sono controlli
normali (tipo presenza) da aggiungere ai test che li richiedono, riconosciuti dal codice.
"""

from django.db import migrations

CONTROLLI = [
    ("distinta", "Distinta", "Distinta base presente", True),
    ("disegno", "Disegno", "Disegno presente", False),
]


def crea(apps, schema_editor):
    Controllo = apps.get_model("tracciabilita", "Controllo")
    for codice, nome, descrizione, predefinito in CONTROLLI:
        Controllo.objects.get_or_create(
            codice=codice,
            defaults={"nome": nome, "descrizione": descrizione, "tipo": "PRE", "presente_predefinito": predefinito},
        )


class Migration(migrations.Migration):
    dependencies = [("tracciabilita", "0001_initial")]

    operations = [migrations.RunPython(crea, migrations.RunPython.noop)]
