from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0021_pna_external_expertise_identified"),
    ]

    operations = [
        migrations.AddField(
            model_name="pnaproject",
            name="consultari_publice_descriere",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="pnaproject",
            name="consultari_publice_locatie",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="pnaproject",
            name="consultari_publice_ora",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]
