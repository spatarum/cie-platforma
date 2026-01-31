from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0004_add_cluster_culoare"),
    ]

    operations = [
        migrations.AddField(
            model_name="expertprofile",
            name="arhivat",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="expertprofile",
            name="arhivat_la",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="expertprofile",
            name="numar_logari",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="expertprofile",
            name="ultima_logare_la",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="questionnaire",
            name="arhivat",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="questionnaire",
            name="arhivat_la",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
