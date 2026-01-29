# Generated manually for the MVP (compatible with Django 4.2+)

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Cluster",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cod", models.PositiveSmallIntegerField(unique=True)),
                ("denumire", models.CharField(max_length=200)),
                ("descriere", models.TextField(blank=True)),
                (
                    "pictograma",
                    models.CharField(
                        blank=True,
                        help_text="Clasa Bootstrap Icons, de ex. 'bi-shield-check'.",
                        max_length=100,
                    ),
                ),
                ("ordonare", models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                "verbose_name": "Cluster",
                "verbose_name_plural": "Clustere",
                "ordering": ["ordonare", "cod"],
            },
        ),
        migrations.CreateModel(
            name="Criterion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cod", models.CharField(max_length=10, unique=True)),
                ("denumire", models.CharField(max_length=255)),
                ("pictograma", models.CharField(blank=True, max_length=100)),
            ],
            options={
                "verbose_name": "Criteriu",
                "verbose_name_plural": "Criterii",
                "ordering": ["cod"],
            },
        ),
        migrations.CreateModel(
            name="Questionnaire",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("titlu", models.CharField(max_length=255)),
                ("descriere", models.TextField(blank=True)),
                (
                    "termen_limita",
                    models.DateTimeField(help_text="După termen, răspunsurile nu mai pot fi editate."),
                ),
                ("creat_la", models.DateTimeField(auto_now_add=True)),
                (
                    "creat_de",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Chestionar",
                "verbose_name_plural": "Chestionare",
                "ordering": ["-termen_limita", "-creat_la"],
            },
        ),
        migrations.CreateModel(
            name="Chapter",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("numar", models.PositiveSmallIntegerField(unique=True)),
                ("denumire", models.CharField(max_length=255)),
                (
                    "pictograma",
                    models.CharField(
                        blank=True,
                        help_text="Clasa Bootstrap Icons, de ex. 'bi-journal-text'.",
                        max_length=100,
                    ),
                ),
                (
                    "cluster",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="capitole",
                        to="portal.cluster",
                    ),
                ),
            ],
            options={
                "verbose_name": "Capitol",
                "verbose_name_plural": "Capitole",
                "ordering": ["numar"],
            },
        ),
        migrations.CreateModel(
            name="Submission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[("DRAFT", "Ciornă"), ("TRIMIS", "Trimis")],
                        default="DRAFT",
                        max_length=10,
                    ),
                ),
                ("creat_la", models.DateTimeField(auto_now_add=True)),
                ("actualizat_la", models.DateTimeField(auto_now=True)),
                ("trimis_la", models.DateTimeField(blank=True, null=True)),
                (
                    "expert",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="submisii",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "questionnaire",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="submisii",
                        to="portal.questionnaire",
                    ),
                ),
            ],
            options={
                "verbose_name": "Răspuns (set)",
                "verbose_name_plural": "Răspunsuri (seturi)",
                "ordering": ["-actualizat_la"],
                "unique_together": {("questionnaire", "expert")},
            },
        ),
        migrations.CreateModel(
            name="Question",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ord", models.PositiveSmallIntegerField()),
                ("text", models.CharField(max_length=1000)),
                (
                    "questionnaire",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="intrebari",
                        to="portal.questionnaire",
                    ),
                ),
            ],
            options={
                "verbose_name": "Întrebare",
                "verbose_name_plural": "Întrebări",
                "ordering": ["ord"],
                "unique_together": {("questionnaire", "ord")},
            },
        ),
        migrations.CreateModel(
            name="ExpertProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("telefon", models.CharField(blank=True, max_length=50)),
                ("organizatie", models.CharField(blank=True, max_length=255)),
                ("functie", models.CharField(blank=True, max_length=255)),
                ("sumar_expertiza", models.CharField(blank=True, max_length=500)),
                (
                    "capitole",
                    models.ManyToManyField(blank=True, related_name="experti", to="portal.chapter"),
                ),
                (
                    "criterii",
                    models.ManyToManyField(blank=True, related_name="experti", to="portal.criterion"),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="profil_expert",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Profil expert",
                "verbose_name_plural": "Profiluri experți",
            },
        ),
        migrations.CreateModel(
            name="Answer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("text", models.CharField(blank=True, max_length=300)),
                (
                    "question",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="raspunsuri",
                        to="portal.question",
                    ),
                ),
                (
                    "submission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="raspunsuri",
                        to="portal.submission",
                    ),
                ),
            ],
            options={
                "verbose_name": "Răspuns",
                "verbose_name_plural": "Răspunsuri",
                "unique_together": {("submission", "question")},
            },
        ),
        migrations.AddField(
            model_name="questionnaire",
            name="capitole",
            field=models.ManyToManyField(blank=True, related_name="chestionare", to="portal.chapter"),
        ),
        migrations.AddField(
            model_name="questionnaire",
            name="criterii",
            field=models.ManyToManyField(blank=True, related_name="chestionare", to="portal.criterion"),
        ),
    ]
