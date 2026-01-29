from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ExpertProfile

User = get_user_model()


@receiver(post_save, sender=User)
def create_profile_for_new_user(sender, instance, created, **kwargs):
    # Pentru admin/superuser nu creăm profil automat (opțional)
    if created and not instance.is_staff:
        ExpertProfile.objects.create(user=instance)
