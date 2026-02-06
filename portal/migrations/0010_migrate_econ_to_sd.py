from django.db import migrations


def forwards(apps, schema_editor):
    Criterion = apps.get_model("portal", "Criterion")
    ExpertProfile = apps.get_model("portal", "ExpertProfile")
    Questionnaire = apps.get_model("portal", "Questionnaire")

    try:
        econ = Criterion.objects.get(cod="ECON")
    except Exception:
        econ = None

    if not econ:
        return

    try:
        sd = Criterion.objects.get(cod="SD")
    except Exception:
        sd = None

    if sd:
        # Move M2M relations from ECON to SD
        for prof in ExpertProfile.objects.filter(criterii=econ).distinct():
            prof.criterii.add(sd)
            prof.criterii.remove(econ)

        for q in Questionnaire.objects.filter(criterii=econ).distinct():
            q.criterii.add(sd)
            q.criterii.remove(econ)

        econ.delete()
    else:
        # Rename ECON in-place
        econ.cod = "SD"
        econ.denumire = "Statul de drept"
        econ.pictograma = "bi-shield-lock"
        econ.culoare = "#7f1d1d"
        econ.save(update_fields=["cod", "denumire", "pictograma", "culoare"])


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0009_expertprofile_pref_text_mare"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
