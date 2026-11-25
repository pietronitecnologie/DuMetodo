from django.db import migrations

# Gruppi di Metodo -> ruoli DuMetodo. Se il nuovo gruppo esiste già, gli utenti vengono spostati.
RINOMINE = {"Geko": "Operatore", "GekoCapRep": "Caporeparto", "Geko Admin": "Amministratore", "Customer": "Cliente"}


def rinomina(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for vecchio, nuovo in RINOMINE.items():
        g = Group.objects.filter(name=vecchio).first()
        if g is None:
            continue
        esistente = Group.objects.filter(name=nuovo).first()
        if esistente is None:
            g.name = nuovo
            g.save(update_fields=["name"])
        else:
            for u in g.user_set.all():
                u.groups.add(esistente)
            g.delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial"), ("auth", "0012_alter_user_first_name_max_length")]
    operations = [migrations.RunPython(rinomina, migrations.RunPython.noop)]
