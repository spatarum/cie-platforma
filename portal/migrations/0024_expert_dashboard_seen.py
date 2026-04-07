from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0023_chat'),
    ]

    operations = [
        migrations.AddField(
            model_name='expertprofile',
            name='expert_dashboard_seen_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
