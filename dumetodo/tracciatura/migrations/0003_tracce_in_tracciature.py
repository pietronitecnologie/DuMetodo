"""Le tracce dell'archivio diventano tracciature del modello «Tracce (da Metodo)», poi l'archivio viene
eliminato (tracciabilita 0006). A blocchi: le tracce importate da Metodo possono essere milioni."""

from django.conf import settings
from django.db import migrations

BLOCCO = 5000


def converti(apps, schema_editor):
    from dumetodo.tracciatura import da_qualita as dq

    Traccia = apps.get_model("tracciabilita", "Traccia")
    if not Traccia.objects.exists():
        return
    m = dq.Modelli(
        Modello=apps.get_model("tracciatura", "Modello"),
        Campo=apps.get_model("tracciatura", "Campo"),
        Tracciatura=apps.get_model("tracciatura", "Tracciatura"),
        Valore=apps.get_model("tracciatura", "Valore"),
        User=apps.get_model(*settings.AUTH_USER_MODEL.split(".")),
    )
    modello, campi = dq.modello_tracce(m)
    sistema = dq.utente_sistema(m).pk
    ultimo = 0
    while True:
        righe = list(
            Traccia.objects.filter(pk__gt=ultimo)
            .order_by("pk")
            .values("pk", "data", "inserita_da_id", "ordine_id", "elemento__codice", "chiave", "valore", "reparto__nome")[
                :BLOCCO
            ]
        )
        if not righe:
            return
        dq.tracce_in_blocco(
            m,
            modello,
            campi,
            [
                {
                    "momento": r["data"],
                    "operatore_id": r["inserita_da_id"] or sistema,
                    "ordine_id": r["ordine_id"],
                    "codice": r["elemento__codice"] or "",
                    "chiave": r["chiave"],
                    "valore": r["valore"],
                    "reparto": r["reparto__nome"] or "",
                }
                for r in righe
            ],
        )
        ultimo = righe[-1]["pk"]


class Migration(migrations.Migration):
    dependencies = [
        ("tracciatura", "0002_vecchie_funzioni_in_tracciature"),
        ("tracciabilita", "0005_rimuovi_qualita_e_file"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(converti, migrations.RunPython.noop)]
