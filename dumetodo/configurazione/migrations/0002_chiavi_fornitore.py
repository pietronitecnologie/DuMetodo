from django.db import migrations

# L'ordine conta: prima si libera "magazzino_fornitore" (che indicava il magazzino del cliente).
RINOMINE = [
    ("email.magazzino_fornitore", "email.magazzino_cliente"),
    ("email.magazzino_geko", "email.magazzino_fornitore"),
    ("email.geko", "email.fornitore"),
]


def rinomina(apps, schema_editor):
    Valore = apps.get_model("configurazione", "ValoreParametro")
    for vecchia, nuova in RINOMINE:
        if not Valore.objects.filter(chiave=nuova).exists():
            Valore.objects.filter(chiave=vecchia).update(chiave=nuova)


class Migration(migrations.Migration):
    dependencies = [("configurazione", "0001_initial")]
    operations = [migrations.RunPython(rinomina, migrations.RunPython.noop)]
