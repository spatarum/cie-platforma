from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0022_pna_consultari_details'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ChatMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text', models.TextField()),
                ('is_question', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('author', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chat_messages', to=settings.AUTH_USER_MODEL)),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='replies', to='portal.chatmessage')),
                ('tagged_chapters', models.ManyToManyField(blank=True, related_name='chat_messages', to='portal.chapter')),
                ('tagged_criteria', models.ManyToManyField(blank=True, related_name='chat_messages', to='portal.criterion')),
                ('tagged_users', models.ManyToManyField(blank=True, related_name='tagged_in_chat_messages', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Mesaj chat',
                'verbose_name_plural': 'Mesaje chat',
                'ordering': ['created_at', 'id'],
            },
        ),
    ]
