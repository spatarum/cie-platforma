from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0007_questionnaire_general"),
    ]

    operations = [
        migrations.AlterField(
            model_name="answer",
            name="text",
            field=models.CharField(blank=True, max_length=3000),
        ),
    ]
