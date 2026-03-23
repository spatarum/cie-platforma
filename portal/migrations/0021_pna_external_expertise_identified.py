from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0020_pna_expert_contributions_and_staff_comisie"),
    ]

    operations = [
        migrations.AddField(
            model_name="pnaproject",
            name="este_identificata_expertiza_externa",
            field=models.BooleanField(default=False),
        ),
    ]
