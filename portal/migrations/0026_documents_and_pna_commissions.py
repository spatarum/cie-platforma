# Generated manually for experti.md
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


COMMISSIONS = [
    (1, "Comisia juridică, pentru numiri și imunități", "Juridică", "bi-shield-lock"),
    (2, "Comisia pentru economie, buget și finanțe", "Economie", "bi-currency-exchange"),
    (3, "Comisia pentru securitate națională, apărare și ordine publică", "Securitate", "bi-shield-check"),
    (4, "Comisia pentru integrare europeană", "Integrare UE", "bi-stars"),
    (5, "Comisia pentru politică externă", "Externă", "bi-globe-europe-africa"),
    (6, "Comisia pentru drepturile omului și relații interetnice", "Drepturile omului", "bi-people"),
    (7, "Comisia pentru administrație publică și dezvoltare regională", "Administrație", "bi-building-gear"),
    (8, "Comisia pentru cultură, educație, cercetare, tineret, sport și mass-media", "Educație/cultură", "bi-mortarboard"),
    (9, "Comisia pentru agricultură și industrie alimentară", "Agricultură", "bi-flower1"),
    (10, "Comisia pentru protecție socială, sănătate și familie", "Social/sănătate", "bi-heart-pulse"),
    (11, "Comisia pentru mediu, climă și tranziție verde", "Mediu", "bi-tree"),
    (12, "Comisia pentru control al finanțelor publice", "Control finanțe", "bi-clipboard-data"),
]


def seed_commissions(apps, schema_editor):
    ParliamentCommission = apps.get_model("portal", "ParliamentCommission")
    for ordine, nume, nume_scurt, pictograma in COMMISSIONS:
        obj, _ = ParliamentCommission.objects.get_or_create(nume=nume)
        obj.ordine = ordine
        obj.nume_scurt = nume_scurt
        obj.pictograma = pictograma
        obj.activa = True
        obj.save()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("portal", "0025_alter_criterion_options_alter_importrun_kind"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nume", models.CharField(max_length=200, unique=True)),
                ("descriere", models.TextField(blank=True)),
                ("ordine", models.PositiveIntegerField(default=0)),
                ("activa", models.BooleanField(default=True)),
                ("creat_la", models.DateTimeField(auto_now_add=True)),
                ("actualizat_la", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Categorie documente",
                "verbose_name_plural": "Categorii documente",
                "ordering": ["ordine", "nume"],
            },
        ),
        migrations.CreateModel(
            name="ParliamentCommission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nume", models.CharField(max_length=255, unique=True)),
                ("nume_scurt", models.CharField(blank=True, max_length=80)),
                ("pictograma", models.CharField(blank=True, default="bi-building", max_length=100)),
                ("ordine", models.PositiveIntegerField(default=0)),
                ("activa", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "Comisie parlamentară",
                "verbose_name_plural": "Comisii parlamentare",
                "ordering": ["ordine", "nume"],
            },
        ),
        migrations.CreateModel(
            name="PlatformDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("titlu", models.CharField(max_length=300)),
                ("descriere", models.TextField(blank=True)),
                ("fisier", models.FileField(upload_to="documente/%Y/%m/")),
                ("ordine", models.PositiveIntegerField(default=0)),
                ("publicat", models.BooleanField(default=True)),
                ("creat_la", models.DateTimeField(auto_now_add=True)),
                ("actualizat_la", models.DateTimeField(auto_now=True)),
                ("categorie", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="documente", to="portal.documentcategory")),
                ("incarcat_de", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="documente_incarcate", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Document",
                "verbose_name_plural": "Documente",
                "ordering": ["categorie__ordine", "ordine", "titlu"],
            },
        ),
        migrations.AddField(
            model_name="pnaproject",
            name="comisie_responsabila",
            field=models.ForeignKey(blank=True, help_text="Comisia parlamentară responsabilă pentru examinarea proiectului.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="proiecte_pna", to="portal.parliamentcommission"),
        ),
        migrations.AddField(
            model_name="pnaproject",
            name="link_dosar_parlament",
            field=models.URLField(blank=True, default="", help_text="Link către dosarul proiectului pe parlament.md."),
        ),
        migrations.RunPython(seed_commissions, migrations.RunPython.noop),
    ]
