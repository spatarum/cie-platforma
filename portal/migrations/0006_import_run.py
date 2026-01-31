from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0005_soft_delete_and_login_stats"),
    ]

    operations = [
        migrations.CreateModel(
            name="ImportRun",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[("EXPERTI", "Import experți")],
                        default="EXPERTI",
                        max_length=20,
                    ),
                ),
                ("creat_la", models.DateTimeField(auto_now_add=True)),
                ("nume_fisier", models.CharField(blank=True, max_length=255)),
                ("nr_create", models.PositiveIntegerField(default=0)),
                ("nr_actualizate", models.PositiveIntegerField(default=0)),
                ("nr_erori", models.PositiveIntegerField(default=0)),
                ("raport_csv", models.TextField(blank=True)),
                ("cred_csv", models.TextField(blank=True)),
                (
                    "creat_de",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="importuri_create",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Rulare import",
                "verbose_name_plural": "Rulări import",
                "ordering": ["-creat_la"],
            },
        ),
    ]
