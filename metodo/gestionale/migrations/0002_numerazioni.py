import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Numerazioni (sezionali): struttura. I dati esistenti si convertono in 0003."""

    dependencies = [("gestionale", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="Numerazione",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tipo", models.CharField(choices=[("DDT", "DDT"), ("FAT", "Fatture e note di credito")], max_length=3)),
                ("nome", models.CharField(max_length=60)),
                (
                    "formato",
                    models.CharField(
                        default="{numero}",
                        help_text="{numero} = progressivo dell'anno, {anno} = anno. Es. {numero}/{anno}.",
                        max_length=30,
                    ),
                ),
                ("predefinita", models.BooleanField(default=False)),
                ("attiva", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "numerazione",
                "verbose_name_plural": "numerazioni",
                "ordering": ["tipo", "-predefinita", "nome"],
                "constraints": [
                    models.UniqueConstraint(fields=("tipo", "nome"), name="numerazione_nome_unico"),
                    models.UniqueConstraint(fields=("tipo", "formato"), name="numerazione_formato_unico"),
                    models.UniqueConstraint(
                        condition=models.Q(("predefinita", True)), fields=("tipo",), name="numerazione_una_predefinita"
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="numeratore",
            name="numerazione",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="contatori",
                to="gestionale.numerazione",
            ),
        ),
        migrations.AddField(
            model_name="fattura",
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
