"""Le vecchie funzioni diventano Tracciature, senza perdere dati (poi vengono eliminate: tracciabilita 0005).

- Controlli qualità: ogni test un modello, ogni esecuzione una tracciatura;
- Foto e file degli ordini: tracciature del modello «Foto e file dell'ordine» (i file restano dove sono);
- Lotto e busta: un modello pronto da usare con Nuova tracciatura.
"""

import datetime as dt

from django.conf import settings
from django.db import migrations
from django.utils import timezone


def converti(apps, schema_editor):
    from dumetodo.tracciatura import da_qualita as dq

    Test = apps.get_model("tracciabilita", "Test")
    ControlloQualita = apps.get_model("tracciabilita", "ControlloQualita")
    FileOrdine = apps.get_model("tracciabilita", "FileOrdine")
    m = dq.Modelli(
        Modello=apps.get_model("tracciatura", "Modello"),
        Campo=apps.get_model("tracciatura", "Campo"),
        Tracciatura=apps.get_model("tracciatura", "Tracciatura"),
        Valore=apps.get_model("tracciatura", "Valore"),
        User=apps.get_model(*settings.AUTH_USER_MODEL.split(".")),
    )
    Traccia = apps.get_model("tracciabilita", "Traccia")
    if Traccia.objects.filter(chiave__in=["LOTTO", "BUSTA"]).exists():  # lotto/busta era in uso
        dq.modello_lotto(m)
    sistema = None
    if FileOrdine.objects.exists():
        modello, campo = dq.modello_file(m)
        for f in FileOrdine.objects.order_by("pk"):
            if f.caricato_da_id is None:
                sistema = sistema or dq.utente_sistema(m)
            dq.registra_file(
                m,
                modello,
                campo,
                ordine=f.ordine,
                percorso=f.file.name,
                nome=f.nome_originale,
                tipo=f.tipo_contenuto,
                momento=f.caricato_il,
                operatore=f.caricato_da or sistema,
            )
    modelli = {}
    for test in Test.objects.prefetch_related("controlli").order_by("pk"):
        controlli = [dq.Controllo(c.pk, c.nome, c.tipo == "PRE") for c in test.controlli.all().order_by("tipo", "nome")]
        modelli[test.pk] = dq.crea_modello(m, test.nome, controlli, test.descrizione, test.attivo)
    for cq in ControlloQualita.objects.prefetch_related("esiti__controllo").order_by("pk"):
        mq = modelli[cq.test_id]
        esiti = {}
        for e in cq.esiti.all():
            dq.aggiungi_controllo(m, mq, dq.Controllo(e.controllo_id, e.controllo.nome, e.controllo.tipo == "PRE"))
            esiti[e.controllo_id] = e.presente if e.presente is not None else (e.campione, e.ko)
        operatore = cq.eseguito_da
        if operatore is None:
            sistema = sistema or dq.utente_sistema(m)
            operatore = sistema
        momento = cq.creato_il
        if timezone.localtime(momento).date() != cq.data:  # data del controllo diversa dall'inserimento
            momento = timezone.make_aware(dt.datetime.combine(cq.data, dt.time(12)))
        dq.registra(
            m,
            mq,
            esiti,
            operatore=operatore,
            momento=momento,
            ordine=cq.ordine,
            articolo=cq.articolo,
            riferimento=cq.riferimento,
            qta=cq.qta_in_ordine,
            note="Dai Controlli qualità",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("tracciatura", "0001_initial"),
        ("tracciabilita", "0004_rimuovi_batterie_cavi_postazioni"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(converti, migrations.RunPython.noop)]
