from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save, m2m_changed
from django.dispatch import receiver
from django.utils import timezone

from .models import ExpertProfile
from .stats import freeze_closed_questionnaires_for_chapters, freeze_closed_questionnaires_for_criteria

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


@receiver(m2m_changed, sender=ExpertProfile.capitole.through)
def freeze_closed_questionnaires_on_chapter_allocation_change(sender, instance, action, reverse, pk_set, **kwargs):
    """Îngheață ratele pentru chestionarele închise înainte de schimbarea alocărilor pe capitole.

    Motiv: dacă un chestionar este deja închis, iar ulterior se adaugă/șterg experți din capitol,
    rata acelui chestionar nu trebuie să se recalibreze.
    """

    if reverse:
        # Nu folosim direcția inversă în fluxurile noastre; ignorăm pentru siguranță.
        return

    if action in ("pre_add", "pre_remove"):
        freeze_closed_questionnaires_for_chapters(pk_set or [])
    elif action == "pre_clear":
        freeze_closed_questionnaires_for_chapters(instance.capitole.values_list("id", flat=True))


@receiver(m2m_changed, sender=ExpertProfile.criterii.through)
def freeze_closed_questionnaires_on_criteria_allocation_change(sender, instance, action, reverse, pk_set, **kwargs):
    """Îngheață ratele pentru chestionarele închise înainte de schimbarea alocărilor pe criterii."""

    if reverse:
        return

    if action in ("pre_add", "pre_remove"):
        freeze_closed_questionnaires_for_criteria(pk_set or [])
    elif action == "pre_clear":
        freeze_closed_questionnaires_for_criteria(instance.criterii.values_list("id", flat=True))
