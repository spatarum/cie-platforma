from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0011_newsletter"),
    ]

    operations = [
        migrations.CreateModel(
            name="QuestionnaireScopeSnapshot",
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
                    "scope",
                    models.CharField(
                        choices=[
                            ("GENERAL", "General"),
                            ("CHAPTER", "Capitol"),
                            ("CRITERION", "Foaie de parcurs"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                ("scope_key", models.CharField(db_index=True, max_length=64)),
                (
                    "frozen_for_deadline",
                    models.DateTimeField(
                        help_text="Termenul limită al chestionarului pentru care au fost înghețate valorile.",
                    ),
                ),
                ("frozen_la", models.DateTimeField(auto_now_add=True)),
                ("nr_experti", models.PositiveIntegerField(default=0)),
                ("nr_raspunsuri", models.PositiveIntegerField(default=0)),
                ("respondent_ids", models.JSONField(blank=True, default=list)),
                (
                    "chapter",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="questionnaire_scope_snapshots",
                        to="portal.chapter",
                    ),
                ),
                (
                    "criterion",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="questionnaire_scope_snapshots",
                        to="portal.criterion",
                    ),
                ),
                (
                    "questionnaire",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="scope_snapshots",
                        to="portal.questionnaire",
                    ),
                ),
            ],
            options={
                "verbose_name": "Snapshot rată răspuns",
                "verbose_name_plural": "Snapshot-uri rată răspuns",
            },
        ),
        migrations.AddConstraint(
            model_name="questionnairescopesnapshot",
            constraint=models.UniqueConstraint(
                fields=("questionnaire", "scope_key"),
                name="uniq_questionnaire_scope_snapshot",
            ),
        ),
    ]
