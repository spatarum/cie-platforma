from django.db import migrations, models


def map_old_pna_statuses(apps, schema_editor):
    """Mapează statusurile vechi PNA pe noul set de statusuri.

    Notă: Django nu validează choices la nivel DB, dar fără această mapare,
    vechile valori ar apărea ca text brut (ex. "IN_LUCRU_GUVERN").
    """

    PnaProject = apps.get_model("portal", "PnaProject")

    mapping = {
        "NEINCEPUT": "NEINITIAT",
        "IN_LUCRU_GUVERN": "INITIAT_GUVERN",
        "IN_AVIZARE_GUVERN": "AVIZARE_GUVERN",
        "ADOPTAT_GUVERN": "INITIAT_PARLAMENT",
        "IN_AVIZARE_CE": "COORDONARE_CE",
        "IN_PROCEDURA_PARLAMENT": "AVIZARE_PARLAMENT",
        "ADOPTAT_PARLAMENT": "ADOPTAT_FINAL",
    }

    for old, new in mapping.items():
        PnaProject.objects.filter(status_implementare=old).update(status_implementare=new)


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0017_backfill_pna_project_institutions"),
    ]

    operations = [
        migrations.AddField(
            model_name="pnaproject",
            name="intrare_planificata_vigoare",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
        migrations.AddField(
            model_name="pnaproject",
            name="consultari_publice_parlament",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(map_old_pna_statuses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="pnaproject",
            name="status_implementare",
            field=models.CharField(
                choices=[
                    ("NEINITIAT", "Neinițiat"),
                    ("INITIAT_GUVERN", "Inițiat în Guvern"),
                    ("AVIZARE_GUVERN", "În avizare la Guvern"),
                    ("COORDONARE_CE", "În coordonare cu Comisia Europeană"),
                    ("APROBARE_GUVERN", "În aprobare la Guvern"),
                    ("INITIAT_PARLAMENT", "Inițiat în Parlament"),
                    ("AVIZARE_PARLAMENT", "În avizare la Parlament"),
                    ("ADOPTAT_PRIMA_LECTURA", "Adoptat în prima lectură"),
                    ("ADOPTAT_FINAL", "Adoptat în lectura finală de Parlament"),
                ],
                default="NEINITIAT",
                max_length=40,
            ),
        ),
        migrations.RemoveField(
            model_name="pnaproject",
            name="indicator_monitorizare",
        ),
    ]
