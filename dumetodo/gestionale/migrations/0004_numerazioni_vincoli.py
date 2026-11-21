import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gestionale", "0003_numerazioni_dati")]

    operations = [
        migrations.RemoveConstraint(model_name="numeratore", name="numeratore_tipo_anno_unico"),
        migrations.RemoveField(model_name="numeratore", name="tipo"),
        migrations.AlterField(
            model_name="numeratore",
            name="numerazione",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="contatori", to="gestionale.numerazione"
            ),
        ),
        migrations.AddConstraint(
            model_name="numeratore",
            constraint=models.UniqueConstraint(
                fields=("numerazione", "anno"), name="numeratore_numerazione_anno_unico"
            ),
        ),
        migrations.RemoveConstraint(model_name="fattura", name="fattura_numero_unico"),
        migrations.AddConstraint(
            model_name="fattura",
            constraint=models.UniqueConstraint(
                condition=models.Q(("progressivo__isnull", False)),
                fields=("numerazione", "anno", "progressivo"),
                name="fattura_numero_unico",
            ),
        ),
    ]
