# Generated manually (Django not available in build container).

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0013_answer_comments"),
    ]

    operations = [
        migrations.CreateModel(
            name="EUAct",
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
                ("celex", models.CharField(max_length=32, unique=True)),
                ("denumire", models.CharField(max_length=700)),
                ("tip_document", models.CharField(blank=True, max_length=200)),
                ("url", models.URLField(blank=True)),
            ],
            options={
                "verbose_name": "Act UE",
                "verbose_name_plural": "Acte UE",
                "ordering": ["celex"],
            },
        ),
        migrations.CreateModel(
            name="PnaProject",
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
                ("titlu", models.CharField(max_length=700)),
                ("institutie_principala", models.CharField(blank=True, max_length=300)),
                ("institutie_coreponsabila", models.CharField(blank=True, max_length=300)),
                ("termen_aprobare_guvern", models.DateField(blank=True, null=True)),
                ("termen_aprobare_parlament", models.DateField(blank=True, null=True)),
                ("termen_actualizat_aprobare_guvern", models.DateField(blank=True, null=True)),
                ("descriere", models.TextField(blank=True)),
                ("contact_responsabil", models.CharField(blank=True, max_length=300)),
                ("contact_responsabil_email", models.EmailField(blank=True, max_length=254)),
                (
                    "complexitate",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        choices=[
                            (1, "Foarte redusă"),
                            (2, "Redusă"),
                            (3, "Medie"),
                            (4, "Ridicată"),
                            (5, "Foarte ridicată"),
                        ],
                        null=True,
                    ),
                ),
                (
                    "prioritate",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        choices=[(1, "Scăzută"), (2, "Medie"), (3, "Înaltă")],
                        null=True,
                    ),
                ),
                (
                    "expertiza_interna",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        choices=[(1, "Insuficientă"), (2, "Parțială"), (3, "Disponibilă")],
                        null=True,
                    ),
                ),
                ("volum_munca_zile", models.PositiveIntegerField(blank=True, null=True)),
                ("necesita_expertiza_externa", models.BooleanField(default=False)),
                ("disponibilitate_expertiza_externa", models.TextField(blank=True)),
                ("parteneri_societate_civila", models.TextField(blank=True)),
                (
                    "cost_2026",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=14,
                        null=True,
                    ),
                ),
                (
                    "cost_2027",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=14,
                        null=True,
                    ),
                ),
                (
                    "cost_2028",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=14,
                        null=True,
                    ),
                ),
                (
                    "cost_2029",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=14,
                        null=True,
                    ),
                ),
                ("riscuri", models.TextField(blank=True)),
                ("analiza_flexibilitate", models.TextField(blank=True)),
                ("analiza_gestiunea_impactului", models.TextField(blank=True)),
                ("analiza_potential_negociere", models.TextField(blank=True)),
                ("pna_cluster", models.CharField(blank=True, max_length=300)),
                ("pna_prioritate_text", models.CharField(blank=True, max_length=100)),
                ("pna_nr_actiune", models.CharField(blank=True, max_length=50)),
                ("pna_cod_unic", models.CharField(blank=True, max_length=255)),
                ("indicator_monitorizare", models.TextField(blank=True)),
                ("comentariu_pna", models.TextField(blank=True)),
                ("intarziat_2025", models.BooleanField(default=False)),
                ("note_explicative", models.TextField(blank=True)),
                ("partener_de_dezvoltare", models.CharField(blank=True, max_length=300)),
                ("executor_actiune", models.CharField(blank=True, max_length=300)),
                (
                    "cost_total_mii_lei",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=14,
                        null=True,
                    ),
                ),
                (
                    "cost_buget_stat_mii_lei",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=14,
                        null=True,
                    ),
                ),
                (
                    "cost_asistenta_externa_mii_lei",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=14,
                        null=True,
                    ),
                ),
                (
                    "cost_neacoperite_mii_lei",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=14,
                        null=True,
                    ),
                ),
                ("acte_normative_transpunere_existente", models.TextField(blank=True)),
                ("creat_la", models.DateTimeField(auto_now_add=True)),
                ("actualizat_la", models.DateTimeField(auto_now=True)),
                ("arhivat", models.BooleanField(default=False)),
                ("arhivat_la", models.DateTimeField(blank=True, null=True)),
                (
                    "chapter",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="pna_proiecte",
                        to="portal.chapter",
                    ),
                ),
                (
                    "criterion",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="pna_proiecte",
                        to="portal.criterion",
                    ),
                ),
                (
                    "creat_de",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="pna_proiecte_create",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Proiect PNA",
                "verbose_name_plural": "Proiecte PNA",
                "ordering": ["-actualizat_la", "titlu"],
            },
        ),
        migrations.CreateModel(
            name="PnaProjectEUAct",
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
                    "tip_transpunere",
                    models.CharField(
                        blank=True,
                        choices=[("TOTAL", "Transpus total"), ("PARTIAL", "Transpus parțial")],
                        max_length=20,
                    ),
                ),
                (
                    "eu_act",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pna_legaturi",
                        to="portal.euact",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="acte_ue_legaturi",
                        to="portal.pnaproject",
                    ),
                ),
            ],
            options={
                "verbose_name": "Act UE în proiect",
                "verbose_name_plural": "Acte UE în proiecte",
                "unique_together": {("project", "eu_act")},
            },
        ),
        migrations.AddField(
            model_name="pnaproject",
            name="acte_ue",
            field=models.ManyToManyField(
                blank=True,
                related_name="proiecte_pna",
                through="portal.PnaProjectEUAct",
                to="portal.euact",
            ),
        ),
    ]
