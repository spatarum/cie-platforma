from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import ExpertProfile

User = get_user_model()


@receiver(post_save, sender=User)
def create_profile_for_new_user(sender, instance, created, **kwargs):
    # Pentru admin/superuser nu creăm profil automat (opțional)
    if created and not instance.is_staff:
        ExpertProfile.objects.create(user=instance)


@receiver(user_logged_in)
def track_expert_login(sender, request, user, **kwargs):
    """Reține numărul total de logări și ultima logare pentru experți."""
    if not user.is_authenticated or user.is_staff:
        return

    profil, _ = ExpertProfile.objects.get_or_create(user=user)
    profil.numar_logari = (profil.numar_logari or 0) + 1
    profil.ultima_logare_la = timezone.now()
    profil.save(update_fields=["numar_logari", "ultima_logare_la"])
