from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0008_alter_answer_text_3000"),
    ]

    operations = [
        migrations.AddField(
            model_name="expertprofile",
            name="pref_text_mare",
            field=models.BooleanField(default=False),
        ),
    ]
