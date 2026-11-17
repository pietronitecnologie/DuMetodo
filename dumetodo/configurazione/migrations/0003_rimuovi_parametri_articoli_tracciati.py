from django.db import migrations


def rimuovi(apps, schema_editor):
    apps.get_model("configurazione", "ValoreParametro").objects.filter(
        chiave__in=["tracciabilita.articolo_batterie", "tracciabilita.articolo_cavi"]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("configurazione", "0002_chiavi_fornitore")]
    operations = [migrations.RunPython(rimuovi, migrations.RunPython.noop)]
