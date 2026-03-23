from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0019_pna_history"),
    ]

    operations = [
        migrations.AddField(
            model_name="expertprofile",
            name="este_staff_comisie",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="PnaExpertContribution",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("flexibilitate", models.TextField(blank=True)),
                ("compensare", models.TextField(blank=True)),
                ("tranzitie", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "expert",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pna_contributii",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="contributii_experti",
                        to="portal.pnaproject",
                    ),
                ),
            ],
            options={
                "verbose_name": "Contribuție expert PNA",
                "verbose_name_plural": "Contribuții experți PNA",
                "ordering": ["-updated_at", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="pnaexpertcontribution",
            constraint=models.UniqueConstraint(
                fields=("project", "expert"),
                name="uniq_pna_expert_contribution",
            ),
        ),
    ]
