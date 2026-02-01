from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0006_import_run"),
    ]

    operations = [
        migrations.AddField(
            model_name="questionnaire",
            name="este_general",
            field=models.BooleanField(
                default=False,
                help_text="Dacă este bifat, chestionarul este disponibil pentru toți experții (categoria «General»).",
            ),
        ),
    ]
