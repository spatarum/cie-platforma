from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def migrate_ina_comment(apps, schema_editor):
    Contribution = apps.get_model("portal", "PnaExpertContribution")
    # Păstrăm exclusiv contribuțiile Inei Spinei din vechiul câmp Flexibilitate.
    for contribution in Contribution.objects.select_related("expert").all():
        full_name = f"{contribution.expert.first_name} {contribution.expert.last_name}".strip().casefold()
        if full_name == "ina spinei" and (contribution.flexibilitate or "").strip():
            contribution.comentariu = contribution.flexibilitate.strip()
            contribution.flexibilitate = ""
            contribution.compensare = ""
            contribution.tranzitie = ""
            contribution.save(update_fields=["comentariu", "flexibilitate", "compensare", "tranzitie"])
        else:
            contribution.delete()


class Migration(migrations.Migration):
    dependencies = [("portal", "0026_documents_and_pna_commissions")]
    operations = [
        migrations.AddField(model_name="questionnaire", name="linkuri_externe", field=models.TextField(blank=True, default="", help_text="Câte un link către pachetul legislativ pe fiecare rând.")),
        migrations.AddField(model_name="pnaproject", name="data_coraport_cie", field=models.DateField(blank=True, null=True, help_text="Data programată pentru coraport în Comisia pentru integrare europeană.")),
        migrations.AddField(model_name="pnaexpertcontribution", name="comentariu", field=models.TextField(blank=True)),
        migrations.CreateModel(
            name="PnaOpinionPresentationRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expert", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="solicitari_prezentare_pna", to=settings.AUTH_USER_MODEL)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="solicitari_prezentare", to="portal.pnaproject")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(model_name="pnaopinionpresentationrequest", constraint=models.UniqueConstraint(fields=("project", "expert"), name="uniq_pna_opinion_presentation_request")),
        migrations.RunPython(migrate_ina_comment, migrations.RunPython.noop),
    ]
