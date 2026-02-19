# Generated manually (Django not available in build container).

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0012_questionnaire_scope_snapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="answer",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="answer",
            name="comentarii_rezolvat",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="answer",
            name="comentarii_rezolvat_la",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="answer",
            name="comentarii_rezolvat_de",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="answer_threads_rezolvat",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="AnswerComment",
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
                ("text", models.TextField(max_length=2000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "answer_updated_at_snapshot",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "answer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="comentarii",
                        to="portal.answer",
                    ),
                ),
                (
                    "author",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="answer_comments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Comentariu la răspuns",
                "verbose_name_plural": "Comentarii la răspunsuri",
                "ordering": ["created_at"],
            },
        ),
    ]
