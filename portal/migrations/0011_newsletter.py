from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0010_migrate_econ_to_sd"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Newsletter",
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
                ("subiect", models.CharField(max_length=255)),
                (
                    "continut",
                    models.TextField(
                        help_text=(
                            "Textul newsletterului. Poți include hyperlinkuri folosind formatul: "
                            "[text](https://exemplu.md)"
                        )
                    ),
                ),
                ("continut_html", models.TextField(blank=True)),
                ("creat_la", models.DateTimeField(auto_now_add=True)),
                ("trimis_la", models.DateTimeField(blank=True, null=True)),
                ("nr_destinatari", models.PositiveIntegerField(default=0)),
                ("nr_trimise", models.PositiveIntegerField(default=0)),
                ("nr_esecuri", models.PositiveIntegerField(default=0)),
                (
                    "creat_de",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="newsletter_create",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "trimis_de",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="newsletter_trimite",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Newsletter",
                "verbose_name_plural": "Newslettere",
                "ordering": ["-creat_la"],
            },
        ),
    ]
