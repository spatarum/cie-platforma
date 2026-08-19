from django.db import migrations, models


def copy_legacy_scopes(apps, schema_editor):
    PnaProject = apps.get_model("portal", "PnaProject")
    through_chapters = PnaProject.chapters.through
    through_criteria = PnaProject.criteria.through

    chapter_links = []
    criterion_links = []
    for project in PnaProject.objects.all().only("id", "chapter_id", "criterion_id"):
        if project.chapter_id:
            chapter_links.append(
                through_chapters(
                    pnaproject_id=project.id,
                    chapter_id=project.chapter_id,
                )
            )
        if project.criterion_id:
            criterion_links.append(
                through_criteria(
                    pnaproject_id=project.id,
                    criterion_id=project.criterion_id,
                )
            )

    if chapter_links:
        through_chapters.objects.bulk_create(chapter_links, ignore_conflicts=True)
    if criterion_links:
        through_criteria.objects.bulk_create(criterion_links, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0027_pna_single_comment_coraport_requests_questionnaire_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="pnaproject",
            name="chapters",
            field=models.ManyToManyField(
                blank=True,
                help_text="Capitolele de negociere relevante pentru proiect.",
                related_name="pna_proiecte_multiple",
                to="portal.chapter",
            ),
        ),
        migrations.AddField(
            model_name="pnaproject",
            name="criteria",
            field=models.ManyToManyField(
                blank=True,
                help_text="Foile de parcurs relevante pentru proiect.",
                related_name="pna_proiecte_multiple",
                to="portal.criterion",
            ),
        ),
        migrations.RunPython(copy_legacy_scopes, migrations.RunPython.noop),
    ]
