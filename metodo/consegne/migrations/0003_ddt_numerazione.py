import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("consegne", "0002_ddt_dati_di_legge"),
        ("gestionale", "0002_numerazioni"),
    ]

    operations = [
        migrations.AddField(
            model_name="ddt",
            name="numerazione",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="gestionale.numerazione",
            ),
        ),
    ]
