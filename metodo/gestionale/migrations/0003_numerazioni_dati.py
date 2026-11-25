from django.db import migrations

FORMATO_PREDEFINITO = "{numero}"


def crea(apps, schema_editor):
    """Numerazioni predefinite «DDT» e «Fatture» (con il formato già configurato) e collegamento dei dati."""
    Numerazione = apps.get_model("gestionale", "Numerazione")
    Numeratore = apps.get_model("gestionale", "Numeratore")
    Fattura = apps.get_model("gestionale", "Fattura")
    DDT = apps.get_model("consegne", "DDT")
    ValoreParametro = apps.get_model("configurazione", "ValoreParametro")
    formato = FORMATO_PREDEFINITO
    vecchio = ValoreParametro.objects.filter(chiave="gestionale.formato_numero_fattura").first()
    if vecchio and isinstance(vecchio.valore, str) and "{numero}" in vecchio.valore:
        formato = vecchio.valore[:30]
    ddt = Numerazione.objects.create(tipo="DDT", nome="DDT", formato=FORMATO_PREDEFINITO, predefinita=True)
    fat = Numerazione.objects.create(tipo="FAT", nome="Fatture", formato=formato, predefinita=True)
    Numeratore.objects.filter(tipo="DDT").update(numerazione=ddt)
    Numeratore.objects.filter(tipo="FATTURA").update(numerazione=fat)
    Numeratore.objects.filter(numerazione__isnull=True).delete()
    Fattura.objects.filter(progressivo__isnull=False).update(numerazione=fat)
    DDT.objects.filter(origine="DUM").update(numerazione=ddt)


class Migration(migrations.Migration):
    dependencies = [
        ("gestionale", "0002_numerazioni"),
        ("consegne", "0003_ddt_numerazione"),
        ("configurazione", "0001_initial"),
    ]

    operations = [migrations.RunPython(crea, migrations.RunPython.noop)]
