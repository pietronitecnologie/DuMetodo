"""Rinomina in Metodo v2: id_dumetodo -> id_locale (id_metodo è già l'id nel vecchio Metodo)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("migrazione", "0002_codice_elemento")]

    operations = [
        migrations.RemoveIndex(model_name="corrispondenza", name="corrispondenza_dumetodo_idx"),
        migrations.RenameField(model_name="corrispondenza", old_name="id_dumetodo", new_name="id_locale"),
        migrations.AddIndex(
            model_name="corrispondenza",
            index=models.Index(fields=["tabella", "id_locale"], name="corrispondenza_locale_idx"),
        ),
        migrations.AlterField(
            model_name="corrispondenza",
            name="creato",
            field=models.BooleanField(default=True, help_text="Falso se l'oggetto esisteva già in Metodo v2"),
        ),
    ]
