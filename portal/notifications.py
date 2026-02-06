from __future__ import annotations

import logging
from typing import Iterable, Tuple

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMessage
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import Questionnaire, Newsletter


logger = logging.getLogger(__name__)


def _build_expert_questionnaire_url(base_url: str, questionnaire_id: int) -> str:
    """Construiește linkul către pagina de completare a chestionarului (interfața expert)."""
    path = reverse("expert_chestionar", args=[questionnaire_id])
    base = (base_url or "").rstrip("/")
    return f"{base}{path}" if base else path


def _get_site_base_url(request_base_url: str | None = None) -> str:
    """Returnează URL-ul de bază al site-ului.

    Prioritate:
    1) settings.SITE_URL (dacă e setat în environment)
    2) request_base_url (dacă a fost calculat în view)
    """
    site = getattr(settings, "SITE_URL", "") or ""
    site = site.strip().rstrip("/")
    if site:
        return site
    return (request_base_url or "").strip().rstrip("/")


def _expert_recipients_for_questionnaire(q: Questionnaire) -> Iterable[User]:
    """Returnează experții activi care trebuie notificați pentru un chestionar nou."""
    base_qs = User.objects.filter(is_staff=False, is_active=True).exclude(email="")

    if getattr(q, "este_general", False):
        return base_qs.order_by("last_name", "first_name").distinct()

    chapters = list(q.capitole.all())
    criteria = list(q.criterii.all())

    if not chapters and not criteria:
        return base_qs.none()

    return (
        base_qs.filter(
            Q(profil_expert__capitole__in=chapters) | Q(profil_expert__criterii__in=criteria)
        )
        .distinct()
        .order_by("last_name", "first_name")
    )


def send_new_questionnaire_emails(
    questionnaire: Questionnaire,
    *,
    request_base_url: str | None = None,
) -> Tuple[int, int]:
    """Trimite emailuri individuale către experții relevanți când se creează un chestionar nou.

    Returnează (nr_trimise_cu_succes, nr_esecuri).
    """
    base_url = _get_site_base_url(request_base_url)
    link = _build_expert_questionnaire_url(base_url, questionnaire.id)

    termen = timezone.localtime(questionnaire.termen_limita)
    termen_txt = termen.strftime("%d.%m.%Y %H:%M")

    descriere = (questionnaire.descriere or "").strip()
    if descriere:
        descriere = descriere[:600]

    # Context (capitole/criterii) - util pentru email
    if getattr(questionnaire, "este_general", False):
        context_txt = "Categorie: General (pentru toți experții)"
    else:
        caps = list(questionnaire.capitole.all().order_by("numar"))
        crs = list(questionnaire.criterii.all().order_by("cod"))
        parts = []
        if caps:
            parts.append(
                "Capitole: "
                + ", ".join([f"{c.numar} – {c.denumire}" for c in caps])
            )
        if crs:
            parts.append("Foi de parcurs: " + ", ".join([f"{c.cod} – {c.denumire}" for c in crs]))
        context_txt = " | ".join(parts) if parts else ""

    subject = f"[CIE] Chestionar nou: {questionnaire.titlu}".strip()

    ok = 0
    fail = 0

    for u in _expert_recipients_for_questionnaire(questionnaire):
        try:
            nume = (u.get_full_name() or u.username or "").strip()
            salut = f"Bună {nume}," if nume else "Bună,"

            lines = [
                salut,
                "",
                "A fost creat un chestionar nou în platformă.",
                "",
                f"Titlu: {questionnaire.titlu}",
            ]
            if context_txt:
                lines.append(context_txt)
            if descriere:
                lines.extend(["", f"Descriere: {descriere}"])
            lines.extend(
                [
                    "",
                    f"Termen limită: {termen_txt}",
                    f"Link către chestionar: {link}",
                    "",
                    "Mulțumim,",
                    "Echipa Comisiei pentru integrare europeană",
                ]
            )

            msg = EmailMessage(
                subject=subject,
                body="\n".join(lines),
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None) or None,
                to=[u.email],
            )
            msg.send(fail_silently=False)
            ok += 1
        except Exception as e:
            fail += 1
            logger.exception("Eroare trimitere email pentru chestionar %s către %s: %s", questionnaire.id, getattr(u, "email", ""), e)

    return ok, fail


def _build_expert_newsletter_url(base_url: str, newsletter_id: int) -> str:
    path = reverse("expert_newsletter_detail", args=[newsletter_id])
    base = (base_url or "").rstrip("/")
    return f"{base}{path}" if base else path


def send_newsletter_emails(
    newsletter: Newsletter,
    *,
    request_base_url: str | None = None,
) -> Tuple[int, int]:
    """Trimite un newsletter către toți experții activi (un email per expert).

    Returnează (nr_trimise_cu_succes, nr_esecuri).
    """
    base_url = _get_site_base_url(request_base_url)
    link = _build_expert_newsletter_url(base_url, newsletter.id)

    subject = f"[CIE] Newsletter: {newsletter.subiect}".strip()

    # Lista destinatari
    recipients = User.objects.filter(is_staff=False, is_active=True).exclude(email="").order_by("last_name", "first_name").distinct()

    ok = 0
    fail = 0

    # Body text (fallback)
    plain_lines = [
        "Bună,",
        "",
        "Ai primit un newsletter nou în platforma experților.",
        "",
        f"Subiect: {newsletter.subiect}",
        "",
        (newsletter.continut or "").strip(),
        "",
        f"Vezi online: {link}",
        "",
        "Mulțumim,",
        "Echipa Comisiei pentru integrare europeană",
    ]
    plain_body = "\n".join([l for l in plain_lines if l is not None])

    # HTML body
    html_body = f"""
    <div style='font-family: Onest, Arial, sans-serif; font-size: 14px; line-height: 1.5;'>
      <p>Bună,</p>
      <p>Ai primit un newsletter nou în platforma experților.</p>
      <h3 style='margin: 12px 0 8px 0;'>{newsletter.subiect}</h3>
      <div style='margin: 8px 0 16px 0;'>{newsletter.continut_html or ''}</div>
      <p style='margin-top: 16px;'>Vezi online: <a href='{link}' target='_blank' rel='noopener noreferrer'>{link}</a></p>
      <p style='margin-top: 16px;'>Mulțumim,<br>Echipa Comisiei pentru integrare europeană</p>
    </div>
    """

    for u in recipients:
        try:
            msg = EmailMultiAlternatives(
                subject=subject,
                body=plain_body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None) or None,
                to=[u.email],
            )
            msg.attach_alternative(html_body, "text/html")
            msg.send(fail_silently=False)
            ok += 1
        except Exception as e:
            fail += 1
            logger.exception(
                "Eroare trimitere newsletter %s către %s: %s",
                newsletter.id,
                getattr(u, "email", ""),
                e,
            )

    return ok, fail
