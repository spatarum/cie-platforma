from __future__ import annotations

import csv
import io
import secrets
import re
import calendar as pycalendar
from datetime import datetime, timedelta, date
from urllib.parse import urlencode

import openpyxl

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q, Count, Max, Prefetch
from django.db.models.functions import Coalesce
from django.forms import formset_factory
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .exports import export_csv, export_pdf, export_xlsx
from .forms import (
    ChestionarForm,
    ExpertCreateForm,
    ExpertUpdateForm,
    StaffCreateForm,
    StaffUpdateForm,
    ExpertImportCSVForm,
    QuestionnaireImportCSVForm,
    RaspunsChestionarForm,
    ExpertPreferinteForm,
    NewsletterForm,
    PnaProjectForm,
    PnaInstitutionForm,
    PnaEUActInlineForm,
    PnaEUActAttachForm,
    PnaImportXLSXForm,
    PnaExpertContributionForm,
    ChatMessageForm,
    ChatReplyForm,
)
from .models import (
    Answer,
    AnswerComment,
    Chapter,
    Criterion,
    ExpertProfile,
    ImportRun,
    Question,
    Questionnaire,
    Submission,
    Newsletter,
    QuestionnaireScopeSnapshot,
    PnaProject,
    PnaInstitution,
    EUAct,
    PnaProjectEUAct,
    PnaExpertContribution,
    PnaProjectStatusHistory,
    PnaProjectDeadlineHistory,
    ChatMessage,
)
from .notifications import send_new_questionnaire_emails, send_newsletter_emails
from .stats import get_questionnaire_rate_and_counts, ensure_scope_snapshot
from .utils import group_chapters_by_cluster
from .pna_import_utils import build_pna_import_template_bytes, run_pna_import_workbook


def is_admin(user: User) -> bool:
    """Administrator (platformă).

    În platformă distingem 3 tipuri:
      - Expert: user.is_staff == False
      - Staff: user.is_staff == True și user.is_superuser == False (doar vizualizare)
      - Staff comisie: ca Staff, dar cu permisiune suplimentară de editare în PNA
      - Administrator: user.is_staff == True și user.is_superuser == True (editare)

    NOTĂ: păstrăm user.is_staff pentru Django Admin. Pentru a nu permite accesul staff-ului
    la /django-admin/, restricționăm Django Admin la superuser (vezi cie_platform/urls.py).
    """
    return bool(user.is_authenticated and user.is_staff and user.is_superuser)


def is_internal(user: User) -> bool:
    """Utilizator intern (Administrator sau Staff)."""
    return bool(user.is_authenticated and user.is_staff)


def is_staff_user(user: User) -> bool:
    """Staff (doar vizualizare)."""
    return bool(user.is_authenticated and user.is_staff and not user.is_superuser)


def is_staff_comisie(user: User) -> bool:
    """Staff comisie (poate edita proiecte PNA).

    Implementare: toggle pe profil (ExpertProfile.este_staff_comisie).
    """
    if not (user.is_authenticated and user.is_staff and not user.is_superuser):
        return False
    profil = getattr(user, "profil_expert", None)
    return bool(profil and getattr(profil, "este_staff_comisie", False))


def can_edit_pna(user: User) -> bool:
    """Cine poate edita PNA (proiecte, instituții, import)."""
    return bool(is_admin(user) or is_staff_comisie(user))


def is_expert(user: User) -> bool:
    return user.is_authenticated and not user.is_staff


def _get_or_create_profile(user: User) -> ExpertProfile:
    profil = getattr(user, "profil_expert", None)
    if not profil:
        profil = ExpertProfile.objects.create(user=user)
    return profil


def _expert_accessible_qs(user: User):
    profil = _get_or_create_profile(user)
    return (
        Questionnaire.objects.filter(arhivat=False).filter(
            Q(este_general=True)
            | Q(capitole__in=profil.capitole.all())
            | Q(criterii__in=profil.criterii.all())
        )
        .distinct()
        .order_by("termen_limita")
    )


def _expert_can_access(user: User, chestionar: Questionnaire) -> bool:
    if getattr(chestionar, 'arhivat', False):
        return False

    # Chestionarele generale sunt disponibile pentru toți experții
    if getattr(chestionar, "este_general", False):
        return True

    profil = _get_or_create_profile(user)
    expert_chapters = set(profil.capitole.values_list("id", flat=True))
    expert_criteria = set(profil.criterii.values_list("id", flat=True))
    q_chapters = set(chestionar.capitole.values_list("id", flat=True))
    q_criteria = set(chestionar.criterii.values_list("id", flat=True))

    if not q_chapters and not q_criteria:
        return False

    matches_chapters = bool(q_chapters and (expert_chapters & q_chapters))
    matches_criteria = bool(q_criteria and (expert_criteria & q_criteria))
    return matches_chapters or matches_criteria


def _expert_pna_accessible_qs(user: User):
    """Proiecte PNA vizibile pentru expert (după aceleași alocări ca la chestionare).

    Regulă: proiecte atașate capitolelor sau foilor de parcurs alocate expertului.
    """

    profil = _get_or_create_profile(user)
    return (
        PnaProject.objects.filter(arhivat=False)
        .filter(Q(chapter__in=profil.capitole.all()) | Q(criterion__in=profil.criterii.all()))
        .distinct()
        .select_related("chapter", "criterion", "institutie_principala_ref")
        .prefetch_related("acte_ue_legaturi__eu_act")
        .order_by("titlu")
    )


def _pna_scope_label(proiect: PnaProject) -> str:
    if getattr(proiect, 'chapter_id', None) and getattr(proiect, 'chapter', None):
        return f"Cap. {proiect.chapter.numar} — {proiect.chapter.denumire}"
    if getattr(proiect, 'criterion_id', None) and getattr(proiect, 'criterion', None):
        return f"{proiect.criterion.cod} — {proiect.criterion.denumire}"
    return "—"




def _apply_pna_stage_filter_to_qs(qs, stage: str):
    """Aplică filtrul agregat de etapă pentru liste/dashboard PNA.

    stage acceptat:
    - neinitiate
    - guvern
    - parlament
    - adoptat_final
    orice altă valoare => fără filtrare
    """
    stage = (stage or "").strip()
    if not stage:
        return qs
    if stage == "neinitiate":
        return qs.filter(status_implementare=PnaProject.STATUS_NEINITIAT)
    if stage == "guvern":
        return qs.filter(status_implementare__in=[
            PnaProject.STATUS_INITIAT_GUVERN,
            PnaProject.STATUS_AVIZARE_GUVERN,
            PnaProject.STATUS_COORDONARE_CE,
            PnaProject.STATUS_APROBARE_GUVERN,
        ])
    if stage == "parlament":
        return qs.filter(status_implementare__in=[
            PnaProject.STATUS_INITIAT_PARLAMENT,
            PnaProject.STATUS_AVIZARE_PARLAMENT,
        ])
    if stage == "adoptat_final":
        return qs.filter(status_implementare=PnaProject.STATUS_ADOPTAT_FINAL)
    return qs




def _user_role_label(user: User) -> str:
    if getattr(user, "is_superuser", False):
        return "Administrator"
    if getattr(user, "is_staff", False):
        profil = getattr(user, "profil_expert", None)
        if profil and getattr(profil, "este_staff_comisie", False):
            return "Staff comisie"
        return "Staff"
    return "Expert"


def _chat_threads_qs(limit: int = 50):
    return (
        ChatMessage.objects.filter(parent__isnull=True)
        .select_related("author")
        .prefetch_related(
            "tagged_chapters",
            "tagged_criteria",
            "tagged_users",
            Prefetch(
                "replies",
                queryset=ChatMessage.objects.select_related("author")
                .prefetch_related("tagged_chapters", "tagged_criteria", "tagged_users")
                .order_by("created_at", "id"),
            ),
        )
        .order_by("-created_at", "-id")[:limit]
    )


def _render_chat_threads_html(request) -> str:
    threads = _chat_threads_qs()
    return render_to_string(
        "portal/chat_messages.html",
        {"threads": threads, "reply_form": ChatReplyForm()},
        request=request,
    )


@login_required
def chat_page(request):
    form = ChatMessageForm(user=request.user)
    return render(
        request,
        "portal/chat.html",
        {
            "form": form,
            "threads": _chat_threads_qs(),
            "reply_form": ChatReplyForm(),
        },
    )


@login_required
def chat_messages_fragment(request):
    html = _render_chat_threads_html(request)
    return JsonResponse({"html": html})


@login_required
def chat_message_create(request):
    if request.method != "POST":
        return redirect("chat_page")

    form = ChatMessageForm(request.POST, user=request.user)
    if form.is_valid():
        msg = form.save(commit=False)
        msg.author = request.user
        msg.parent = None
        msg.save()
        form.save_m2m()
        messages.success(request, "Mesajul a fost publicat în chat.")
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "html": _render_chat_threads_html(request)})
        return redirect(f"{reverse('chat_page')}#msg-{msg.id}")

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        html = render_to_string("portal/chat_compose_form.html", {"form": form}, request=request)
        return JsonResponse({"ok": False, "form_html": html}, status=400)

    return render(
        request,
        "portal/chat.html",
        {"form": form, "threads": _chat_threads_qs(), "reply_form": ChatReplyForm()},
        status=400,
    )


@login_required
def chat_reply_create(request, parent_id: int):
    parent = get_object_or_404(ChatMessage.objects.select_related("author"), pk=parent_id, parent__isnull=True)
    if request.method != "POST":
        return redirect(f"{reverse('chat_page')}#msg-{parent.id}")

    form = ChatReplyForm(request.POST)
    if form.is_valid():
        reply = form.save(commit=False)
        reply.author = request.user
        reply.parent = parent
        reply.is_question = False
        reply.save()
        # moștenim etichetele de context ale întrebării/discuției principale
        reply.tagged_chapters.set(parent.tagged_chapters.all())
        reply.tagged_criteria.set(parent.tagged_criteria.all())
        reply.tagged_users.set(parent.tagged_users.all())
        messages.success(request, "Răspunsul a fost publicat.")
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "html": _render_chat_threads_html(request)})
        return redirect(f"{reverse('chat_page')}#msg-{parent.id}")

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": False, "errors": form.errors}, status=400)
    messages.error(request, "Răspunsul nu a putut fi salvat. Verifică textul introdus.")
    return redirect(f"{reverse('chat_page')}#msg-{parent.id}")


def _shift_year_month(year: int, month: int, delta_months: int) -> tuple[int, int]:
    month0 = month - 1 + delta_months
    year += month0 // 12
    month = month0 % 12 + 1
    return year, month


def _build_consultari_calendar_context(proiecte, detail_url_name: str, year: int | None = None, month: int | None = None):
    today = timezone.localdate()
    year = year or today.year
    month = month or today.month

    first_weekday, num_days = pycalendar.monthrange(year, month)  # Mon=0
    month_first = date(year, month, 1)
    start_date = month_first - timedelta(days=first_weekday)

    events_by_day: dict[date, list[dict]] = {}
    future_events = []
    past_events = []

    for proiect in proiecte:
        consult_date = getattr(proiect, 'consultari_publice_parlament', None)
        if not consult_date:
            continue
        item = {
            'project': proiect,
            'date': consult_date,
            'time': (getattr(proiect, 'consultari_publice_ora', '') or '').strip(),
            'location': (getattr(proiect, 'consultari_publice_locatie', '') or '').strip(),
            'description': (getattr(proiect, 'consultari_publice_descriere', '') or '').strip(),
            'scope_label': _pna_scope_label(proiect),
            'detail_url': reverse(detail_url_name, kwargs={'pk': proiect.pk}),
        }
        events_by_day.setdefault(consult_date, []).append(item)
        if consult_date >= today:
            future_events.append(item)
        else:
            past_events.append(item)

    for items in events_by_day.values():
        items.sort(key=lambda x: ((x['time'] or '99:99'), x['project'].titlu.lower()))
    future_events.sort(key=lambda x: (x['date'], x['time'] or '99:99', x['project'].titlu.lower()))
    past_events.sort(key=lambda x: (x['date'], x['time'] or '99:99', x['project'].titlu.lower()), reverse=True)

    weeks = []
    cursor = start_date
    for _ in range(6):
        row = []
        for _ in range(7):
            day_events = events_by_day.get(cursor, [])
            row.append({
                'date': cursor,
                'day': cursor.day,
                'in_month': cursor.month == month,
                'is_today': cursor == today,
                'events': day_events,
                'count': len(day_events),
            })
            cursor += timedelta(days=1)
        weeks.append(row)

    prev_year, prev_month = _shift_year_month(year, month, -1)
    next_year, next_month = _shift_year_month(year, month, 1)

    month_event_count = sum(len(events_by_day.get(date(year, month, d), [])) for d in range(1, num_days + 1))

    return {
        'selected_year': year,
        'selected_month': month,
        'calendar_weeks': weeks,
        'weekday_names': ['Lun', 'Mar', 'Mie', 'Joi', 'Vin', 'Sâm', 'Dum'],
        'month_options': [(1, 'Ianuarie'), (2, 'Februarie'), (3, 'Martie'), (4, 'Aprilie'), (5, 'Mai'), (6, 'Iunie'), (7, 'Iulie'), (8, 'August'), (9, 'Septembrie'), (10, 'Octombrie'), (11, 'Noiembrie'), (12, 'Decembrie')],
        'month_label': month_first,
        'month_event_count': month_event_count,
        'upcoming_events': future_events[:12],
        'past_events': past_events[:12],
        'prev_year': prev_year,
        'prev_month': prev_month,
        'next_year': next_year,
        'next_month': next_month,
        'today_year': today.year,
        'today_month': today.month,
    }


@login_required
def home(request):
    if request.user.is_staff:
        return redirect("admin_dashboard")
    return redirect("expert_dashboard")


# -------------------- EXPERT --------------------


@user_passes_test(is_expert)
def expert_dashboard(request):
    profil = _get_or_create_profile(request.user)
    qs = _expert_accessible_qs(request.user)

    # Filtre (opțional) după categorie/capitol/criteriu
    general = request.GET.get("general")
    cap_id = request.GET.get("capitol")
    cr_id = request.GET.get("criteriu")

    active_cap_id = None
    active_cr_id = None
    active_general = False

    # Prioritate: General -> Capitol -> Criteriu
    if general:
        qs = qs.filter(este_general=True).distinct()
        active_general = True
    elif cap_id:
        try:
            cap_id_int = int(cap_id)
        except (TypeError, ValueError):
            cap_id_int = None
        if cap_id_int and profil.capitole.filter(id=cap_id_int).exists():
            qs = qs.filter(capitole__id=cap_id_int).distinct()
            active_cap_id = cap_id_int
            active_cr_id = None
    elif cr_id:
        try:
            cr_id_int = int(cr_id)
        except (TypeError, ValueError):
            cr_id_int = None
        if cr_id_int and profil.criterii.filter(id=cr_id_int).exists():
            qs = qs.filter(criterii__id=cr_id_int).distinct()
            active_cr_id = cr_id_int
            active_cap_id = None
    now = timezone.now()
    deschise = qs.filter(termen_limita__gte=now).order_by("termen_limita")
    inchise = qs.filter(termen_limita__lt=now).order_by("-termen_limita")

    sub_map = {
        s.questionnaire_id: s
        for s in Submission.objects.filter(expert=request.user, questionnaire__in=qs)
    }

    return render(
        request,
        "portal/expert_dashboard.html",
        {
            "deschise": deschise,
            "inchise": inchise,
            "sub_map": sub_map,
            "capitole_tile": profil.capitole.all().order_by("numar"),
            "criterii_tile": profil.criterii.all().order_by("cod"),
            "active_capitol": active_cap_id,
            "active_criteriu": active_cr_id,
            "active_general": active_general,
        },
    )


@user_passes_test(is_expert)
def expert_profile(request):
    profil = _get_or_create_profile(request.user)
    return render(request, "portal/expert_profile.html", {"profil": profil})


@user_passes_test(is_expert)
def expert_contacte(request):
    """Contactele experților cu domenii comune.

    Experții pot vedea doar contactele altor experți care au alocate aceleași
    capitole sau foi de parcurs (intersecție non-goală). Telefonul nu este afișat.
    """

    profil = _get_or_create_profile(request.user)
    my_chapters = list(profil.capitole.all().order_by("numar"))
    my_criteria = list(profil.criterii.all().order_by("cod"))

    my_ch_ids = [c.id for c in my_chapters]
    my_cr_ids = [c.id for c in my_criteria]

    base_qs = (
        ExpertProfile.objects.select_related("user")
        .prefetch_related("capitole", "criterii")
        .filter(user__is_staff=False, user__is_active=True, arhivat=False)
        .exclude(user=request.user)
    )

    if my_ch_ids or my_cr_ids:
        other_profiles = (
            base_qs.filter(Q(capitole__id__in=my_ch_ids) | Q(criterii__id__in=my_cr_ids))
            .distinct()
            .order_by("user__last_name", "user__first_name", "user__username")
        )
    else:
        other_profiles = base_qs.none()

    contacts = []
    for p in other_profiles:
        other_ch_ids = {c.id for c in p.capitole.all()}
        other_cr_ids = {c.id for c in p.criterii.all()}

        shared_chapters = [c for c in my_chapters if c.id in other_ch_ids]
        shared_criteria = [c for c in my_criteria if c.id in other_cr_ids]

        # Siguranță suplimentară: păstrăm doar dacă există intersecție reală.
        if not shared_chapters and not shared_criteria:
            continue

        contacts.append(
            {
                "user": p.user,
                "profil": p,
                "shared_chapters": shared_chapters,
                "shared_criteria": shared_criteria,
            }
        )

    # Grupări pentru afișare comodă (doar grupurile care au cel puțin un expert)
    grouped_by_chapter = []
    for ch in my_chapters:
        ch_contacts = [c for c in contacts if ch in c["shared_chapters"]]
        if ch_contacts:
            grouped_by_chapter.append({"ref": ch, "contacts": ch_contacts})

    grouped_by_criteria = []
    for cr in my_criteria:
        cr_contacts = [c for c in contacts if cr in c["shared_criteria"]]
        if cr_contacts:
            grouped_by_criteria.append({"ref": cr, "contacts": cr_contacts})

    return render(
        request,
        "portal/expert_contacte.html",
        {
            "profil": profil,
            "my_chapters": my_chapters,
            "my_criteria": my_criteria,
            "contacts": contacts,
            "grouped_by_chapter": grouped_by_chapter,
            "grouped_by_criteria": grouped_by_criteria,
        },
    )


@user_passes_test(is_expert)
def expert_preferinte(request):
    profil = _get_or_create_profile(request.user)

    pref_form = ExpertPreferinteForm(initial={"text_mare": profil.pref_text_mare})
    pwd_form = PasswordChangeForm(user=request.user)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "prefs":
            pref_form = ExpertPreferinteForm(request.POST)
            pwd_form = PasswordChangeForm(user=request.user)
            if pref_form.is_valid():
                profil.pref_text_mare = pref_form.cleaned_data.get("text_mare", False)
                profil.save(update_fields=["pref_text_mare"])
                messages.success(request, "Preferințele au fost salvate.")
                return redirect("expert_preferinte")

        elif action == "pwd":
            pwd_form = PasswordChangeForm(user=request.user, data=request.POST)
            pref_form = ExpertPreferinteForm(initial={"text_mare": profil.pref_text_mare})
            if pwd_form.is_valid():
                user = pwd_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Parola a fost schimbată.")
                return redirect("expert_preferinte")

    # Stilizare inputuri
    for f in [pwd_form.fields.get("old_password"), pwd_form.fields.get("new_password1"), pwd_form.fields.get("new_password2")]:
        if f and "widget" in dir(f):
            try:
                f.widget.attrs.update({"class": "form-control"})
            except Exception:
                pass

    return render(
        request,
        "portal/expert_preferinte.html",
        {"pref_form": pref_form, "pwd_form": pwd_form},
    )


@user_passes_test(is_expert)
def expert_newsletters(request):
    newsletters = Newsletter.objects.filter(trimis_la__isnull=False).order_by("-trimis_la", "-creat_la")
    return render(request, "portal/expert_newsletters.html", {"newsletters": newsletters})


@user_passes_test(is_expert)
def expert_newsletter_detail(request, pk: int):
    nl = get_object_or_404(Newsletter, pk=pk, trimis_la__isnull=False)
    return render(request, "portal/expert_newsletter_detail.html", {"nl": nl})


@user_passes_test(is_internal)
def admin_pna_consultari(request):
    proiecte = list(
        PnaProject.objects.filter(arhivat=False, consultari_publice_parlament__isnull=False)
        .select_related("chapter", "criterion", "institutie_principala_ref")
        .order_by("consultari_publice_parlament", "titlu")
    )
    try:
        year = int((request.GET.get("year") or "").strip()) if request.GET.get("year") else None
    except Exception:
        year = None
    try:
        month = int((request.GET.get("month") or "").strip()) if request.GET.get("month") else None
    except Exception:
        month = None
    ctx = _build_consultari_calendar_context(proiecte, "admin_pna_detail", year, month)
    ctx.update({
        "page_title": "Calendar consultări publice în Parlament",
        "page_subtitle": "Consultări publice planificate și realizate pentru proiectele PNA.",
        "is_internal_view": True,
        "back_url": reverse("admin_pna_list"),
        "back_label": "Înapoi la PNA",
    })
    return render(request, "portal/pna_consultari_calendar.html", ctx)


@user_passes_test(is_expert)
def expert_pna_consultari(request):
    proiecte = list(
        _expert_pna_accessible_qs(request.user)
        .filter(consultari_publice_parlament__isnull=False)
        .order_by("consultari_publice_parlament", "titlu")
    )
    try:
        year = int((request.GET.get("year") or "").strip()) if request.GET.get("year") else None
    except Exception:
        year = None
    try:
        month = int((request.GET.get("month") or "").strip()) if request.GET.get("month") else None
    except Exception:
        month = None
    ctx = _build_consultari_calendar_context(proiecte, "expert_pna_detail", year, month)
    ctx.update({
        "page_title": "Calendar consultări publice în Parlament",
        "page_subtitle": "Vezi doar consultările din capitolele și foile de parcurs alocate ție.",
        "is_internal_view": False,
        "back_url": reverse("expert_pna_list"),
        "back_label": "Înapoi la PNA",
    })
    return render(request, "portal/pna_consultari_calendar.html", ctx)


@user_passes_test(is_expert)
def expert_pna_list(request):
    """Lista proiectelor PNA vizibile expertului (după alocările sale)."""

    q = (request.GET.get("q") or "").strip()
    stage = (request.GET.get("stage") or "").strip()

    base_qs = _expert_pna_accessible_qs(request.user)
    proiecte_qs = base_qs
    if q:
        proiecte_qs = proiecte_qs.filter(
            Q(titlu__icontains=q)
            | Q(institutie_principala__icontains=q)
            | Q(institutie_principala_ref__nume__icontains=q)
            | Q(acte_ue_legaturi__eu_act__denumire__icontains=q)
            | Q(acte_ue_legaturi__eu_act__celex__icontains=q)
        ).distinct()

    all_projects = list(base_qs)
    total = len(all_projects)
    nr_neinitiate = sum(1 for p in all_projects if p.status_implementare == PnaProject.STATUS_NEINITIAT)
    nr_in_procedura_guvern = sum(1 for p in all_projects if p.status_implementare in {
        PnaProject.STATUS_INITIAT_GUVERN, PnaProject.STATUS_AVIZARE_GUVERN, PnaProject.STATUS_COORDONARE_CE, PnaProject.STATUS_APROBARE_GUVERN
    })
    nr_in_procedura_parlament = sum(1 for p in all_projects if p.status_implementare in {
        PnaProject.STATUS_INITIAT_PARLAMENT, PnaProject.STATUS_AVIZARE_PARLAMENT
    })
    nr_adoptate_final = sum(1 for p in all_projects if p.status_implementare == PnaProject.STATUS_ADOPTAT_FINAL)

    proiecte_qs = _apply_pna_stage_filter_to_qs(proiecte_qs, stage)
    proiecte = list(proiecte_qs)

    # map contribuții (per expert)
    contribs = {
        c.project_id: c
        for c in PnaExpertContribution.objects.filter(
            expert=request.user, project__in=proiecte_qs
        )
    }

    # Grupare pe foi de parcurs + capitole (ca în admin), dar cu afișare limitată.
    by_criterion = {}
    by_chapter = {}
    for p in proiecte:
        if p.criterion_id:
            by_criterion.setdefault(p.criterion_id, []).append(p)
        elif p.chapter_id:
            by_chapter.setdefault(p.chapter_id, []).append(p)

    criterii = list(Criterion.objects.all().order_by("cod"))
    criterii_groups = []
    for cr in criterii:
        rows = by_criterion.get(cr.id, [])
        if rows:
            rows.sort(key=lambda p: (p.termen_aprobare_parlament or datetime.max.date(), p.titlu))
            criterii_groups.append({"criteriu": cr, "proiecte": rows})

    grouped_chapters = group_chapters_by_cluster()
    chapter_groups = []
    for cl, chapters in grouped_chapters:
        ch_rows = []
        for ch in chapters:
            rows = by_chapter.get(ch.id, [])
            if rows:
                rows.sort(key=lambda p: (p.termen_aprobare_parlament or datetime.max.date(), p.titlu))
                ch_rows.append({"capitol": ch, "proiecte": rows})
        if ch_rows:
            chapter_groups.append({"cluster": cl, "chapters": ch_rows})

    return render(
        request,
        "portal/expert_pna_list.html",
        {
            "q": q,
            "criterii_groups": criterii_groups,
            "chapter_groups": chapter_groups,
            "contribs": contribs,
            "stage": stage,
            "total": total,
            "nr_neinitiate": nr_neinitiate,
            "nr_in_procedura_guvern": nr_in_procedura_guvern,
            "nr_in_procedura_parlament": nr_in_procedura_parlament,
            "nr_adoptate_final": nr_adoptate_final,
            "stage": stage,
        },
    )


@user_passes_test(is_expert)
def expert_pna_detail(request, pk: int):
    """Detaliu proiect PNA (expert) + formular contribuții (3 boxe)."""

    proiect = get_object_or_404(
        PnaProject.objects.select_related("chapter", "criterion", "institutie_principala_ref")
        .prefetch_related("acte_ue_legaturi__eu_act"),
        pk=pk,
        arhivat=False,
    )

    # control acces: doar pe capitole/foi de parcurs alocate expertului
    profil = _get_or_create_profile(request.user)
    ok = False
    if proiect.chapter_id and profil.capitole.filter(id=proiect.chapter_id).exists():
        ok = True
    if proiect.criterion_id and profil.criterii.filter(id=proiect.criterion_id).exists():
        ok = True
    if not ok:
        raise Http404("Proiect indisponibil")

    contrib, _ = PnaExpertContribution.objects.get_or_create(project=proiect, expert=request.user)

    if request.method == "POST":
        form = PnaExpertContributionForm(request.POST, instance=contrib)
        if form.is_valid():
            form.save()
            messages.success(request, "Comentariile au fost salvate.")
            return redirect("expert_pna_detail", pk=proiect.pk)
    else:
        form = PnaExpertContributionForm(instance=contrib)

    acts = list(proiect.acte_ue_legaturi.select_related("eu_act").all())

    return render(
        request,
        "portal/expert_pna_detail.html",
        {
            "obj": proiect,
            "acts": acts,
            "form": form,
            "contrib": contrib,
        },
    )


# -------------------- STAFF (read-only) --------------------


@user_passes_test(is_internal)
def staff_newsletters(request):
    """Vizualizare newslettere (doar cele trimise), la fel ca la experți."""
    newsletters = Newsletter.objects.filter(trimis_la__isnull=False).order_by("-trimis_la", "-creat_la")
    return render(request, "portal/expert_newsletters.html", {"newsletters": newsletters})


@user_passes_test(is_internal)
def staff_newsletter_detail(request, pk: int):
    nl = get_object_or_404(Newsletter, pk=pk, trimis_la__isnull=False)
    return render(request, "portal/expert_newsletter_detail.html", {"nl": nl})


@user_passes_test(is_staff_user)
def staff_preferinte(request):
    """Preferințe pentru utilizatori Staff.

    Similar cu preferințele experților:
      - Text mare (accesibilitate)
      - Schimbare parolă
    """

    profil = _get_or_create_profile(request.user)

    pref_form = ExpertPreferinteForm(initial={"text_mare": profil.pref_text_mare})
    pwd_form = PasswordChangeForm(user=request.user)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "prefs":
            pref_form = ExpertPreferinteForm(request.POST)
            pwd_form = PasswordChangeForm(user=request.user)
            if pref_form.is_valid():
                profil.pref_text_mare = pref_form.cleaned_data.get("text_mare", False)
                profil.save(update_fields=["pref_text_mare"])
                messages.success(request, "Preferințele au fost salvate.")
                return redirect("staff_preferinte")

        elif action == "pwd":
            pwd_form = PasswordChangeForm(user=request.user, data=request.POST)
            pref_form = ExpertPreferinteForm(initial={"text_mare": profil.pref_text_mare})
            if pwd_form.is_valid():
                user = pwd_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Parola a fost schimbată.")
                return redirect("staff_preferinte")

    # Stilizare inputuri
    for f in [
        pwd_form.fields.get("old_password"),
        pwd_form.fields.get("new_password1"),
        pwd_form.fields.get("new_password2"),
    ]:
        if f and "widget" in dir(f):
            try:
                f.widget.attrs.update({"class": "form-control"})
            except Exception:
                pass

    return render(
        request,
        "portal/expert_preferinte.html",
        {"pref_form": pref_form, "pwd_form": pwd_form},
    )


@user_passes_test(is_expert)
def expert_questionnaire(request, pk: int):
    chestionar = get_object_or_404(Questionnaire, pk=pk)
    if not _expert_can_access(request.user, chestionar):
        raise Http404("Chestionar indisponibil")

    submission, _ = Submission.objects.get_or_create(questionnaire=chestionar, expert=request.user)
    editabil = submission.poate_edita

    if request.method == "POST":
        if not editabil:
            messages.error(request, "Termenul limită a expirat. Nu mai poți modifica răspunsurile.")
            return redirect("expert_chestionar", pk=pk)

        form = RaspunsChestionarForm(request.POST, questionnaire=chestionar, submission=submission)
        if form.is_valid():
            form.save()
            actiune = request.POST.get("actiune", "salveaza")
            if actiune == "trimite":
                submission.status = Submission.STATUS_TRIMIS
                submission.trimis_la = timezone.now()
                submission.save(update_fields=["status", "trimis_la", "actualizat_la"])
                messages.success(request, "Răspunsurile au fost trimise.")
            else:
                # Dacă a fost deja trimis, păstrăm statusul TRIMIS, dar permitem actualizarea răspunsurilor până la termen.
                if submission.status != Submission.STATUS_TRIMIS:
                    submission.status = Submission.STATUS_DRAFT
                submission.save(update_fields=["status", "actualizat_la"])
                if submission.status == Submission.STATUS_TRIMIS:
                    messages.success(request, "Răspunsurile au fost salvate (trimise anterior).")
                else:
                    messages.success(request, "Ciorna a fost salvată.")
            return redirect("expert_chestionar", pk=pk)
    else:
        form = RaspunsChestionarForm(questionnaire=chestionar, submission=submission)

    # Comentarii (staff/admin) pe fiecare răspuns – vizibile expertului.
    # Cheie: numele câmpului din form (q_<question_id>)
    answers_qs = (
        Answer.objects.filter(submission=submission)
        .select_related("question", "comentarii_rezolvat_de")
        .prefetch_related("comentarii", "comentarii__author")
    )
    comentarii_map = {}
    for a in answers_qs:
        comms = list(a.comentarii.all())
        last = comms[-1] if comms else None
        modified_after_last_comment = False
        if last:
            if last.answer_updated_at_snapshot and a.updated_at:
                modified_after_last_comment = a.updated_at > last.answer_updated_at_snapshot
            elif a.updated_at:
                modified_after_last_comment = a.updated_at > last.updated_at

        comentarii_map[f"q_{a.question_id}"] = {
            "answer": a,
            "comments": comms,
            "thread_rezolvat": bool(a.comentarii_rezolvat),
            "thread_rezolvat_la": a.comentarii_rezolvat_la,
            "thread_rezolvat_de": a.comentarii_rezolvat_de,
            "answer_modified_after_last_comment": modified_after_last_comment,
        }

    return render(
        request,
        "portal/expert_chestionar.html",
        {
            "chestionar": chestionar,
            "form": form,
            "submission": submission,
            "editabil": editabil,
            "comentarii_map": comentarii_map,
        },
    )


# -------------------- ADMIN --------------------


@user_passes_test(is_internal)
def admin_dashboard(request):
    chestionare = Questionnaire.objects.filter(arhivat=False).order_by("-creat_la")[:10]
    nr_experti = User.objects.filter(is_staff=False, is_active=True).count()
    nr_chestionare_total = Questionnaire.objects.filter(arhivat=False).count()

    # ------------------------------
    # Statistici sintetice: Capitole & foi de parcurs
    # ------------------------------
    criterii = list(Criterion.objects.all().order_by("cod"))
    grouped_chapters = group_chapters_by_cluster()

    # 1) Alocări (doar experți activi, non-admin)
    profiles = (
        ExpertProfile.objects.select_related("user")
        .filter(user__is_active=True, user__is_staff=False)
        .prefetch_related("capitole", "criterii")
    )

    chapter_alloc: dict[int, set[int]] = {}
    criterion_alloc: dict[int, set[int]] = {}
    for p in profiles:
        uid = p.user_id
        for ch in p.capitole.all():
            chapter_alloc.setdefault(ch.id, set()).add(uid)
        for cr in p.criterii.all():
            criterion_alloc.setdefault(cr.id, set()).add(uid)

    # 2) Chestionare (doar nearhivate)
    q_all = (
        Questionnaire.objects.filter(arhivat=False)
        .prefetch_related("capitole", "criterii")
        .only("id", "termen_limita")
    )
    q_by_id: dict[int, Questionnaire] = {q.id: q for q in q_all}

    chapter_q: dict[int, set[int]] = {}
    criterion_q: dict[int, set[int]] = {}
    for q in q_all:
        qid = q.id
        for ch in q.capitole.all():
            chapter_q.setdefault(ch.id, set()).add(qid)
        for cr in q.criterii.all():
            criterion_q.setdefault(cr.id, set()).add(qid)

    now = timezone.now()

    def _avg(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    # ---- Foi de parcurs (criterii) ----
    criterii_stats = []
    for cr in criterii:
        alloc = criterion_alloc.get(cr.id, set())
        nr_experti_cr = len(alloc)

        qids = list(criterion_q.get(cr.id, set()))
        nr_chestionare_cr = len(qids)
        if nr_chestionare_cr == 0:
            criterii_stats.append(
                {
                    "obj": cr,
                    "nr_experti": nr_experti_cr,
                    "nr_chestionare": 0,
                    "nr_raspunsuri": 0,
                    "rata_medie_raspuns": 0.0,
                }
            )
            continue

        open_ids = [qid for qid in qids if q_by_id.get(qid) and q_by_id[qid].termen_limita >= now]
        closed_ids = [qid for qid in qids if q_by_id.get(qid) and q_by_id[qid].termen_limita < now]

        # Open: număr submisii TRIMIS per chestionar (în cadrul criteriului)
        open_counts: dict[int, int] = {}
        if open_ids:
            rows = (
                Submission.objects.filter(
                    status=Submission.STATUS_TRIMIS,
                    questionnaire_id__in=open_ids,
                    questionnaire__arhivat=False,
                    expert__is_active=True,
                    expert__is_staff=False,
                    expert__profil_expert__criterii=cr,
                )
                .values("questionnaire_id")
                .annotate(cnt=Count("id", distinct=True))
            )
            open_counts = {r["questionnaire_id"]: r["cnt"] for r in rows}

        # Closed: snapshot-uri înghețate
        scope_key = QuestionnaireScopeSnapshot.make_scope_key(
            QuestionnaireScopeSnapshot.SCOPE_CRITERION, criterion_id=cr.id
        )
        snap_map: dict[int, QuestionnaireScopeSnapshot] = {
            s.questionnaire_id: s
            for s in QuestionnaireScopeSnapshot.objects.filter(
                questionnaire_id__in=closed_ids,
                scope_key=scope_key,
            )
        }
        for qid in closed_ids:
            if qid not in snap_map and q_by_id.get(qid):
                snap_map[qid] = ensure_scope_snapshot(
                    q_by_id[qid],
                    scope=QuestionnaireScopeSnapshot.SCOPE_CRITERION,
                    criterion=cr,
                )

        # Agregare
        total_responses = 0
        rates: list[float] = []

        for qid in open_ids:
            num = int(open_counts.get(qid, 0))
            total_responses += num
            den = nr_experti_cr
            rates.append(round((num / den) * 100, 1) if den else 0.0)

        for qid in closed_ids:
            snap = snap_map.get(qid)
            if snap:
                total_responses += int(snap.nr_raspunsuri or 0)
                rates.append(float(snap.rata or 0.0))
            else:
                rates.append(0.0)

        criterii_stats.append(
            {
                "obj": cr,
                "nr_experti": nr_experti_cr,
                "nr_chestionare": nr_chestionare_cr,
                "nr_raspunsuri": total_responses,
                "rata_medie_raspuns": _avg(rates),
            }
        )

    # ---- Capitole (grupate pe clustere) ----
    grouped_chapter_stats = []
    for cl, chapters in grouped_chapters:
        chapter_rows = []
        for ch in chapters:
            alloc = chapter_alloc.get(ch.id, set())
            nr_experti_ch = len(alloc)

            qids = list(chapter_q.get(ch.id, set()))
            nr_chestionare_ch = len(qids)
            if nr_chestionare_ch == 0:
                chapter_rows.append(
                    {
                        "obj": ch,
                        "nr_experti": nr_experti_ch,
                        "nr_chestionare": 0,
                        "nr_raspunsuri": 0,
                        "rata_medie_raspuns": 0.0,
                    }
                )
                continue

            open_ids = [qid for qid in qids if q_by_id.get(qid) and q_by_id[qid].termen_limita >= now]
            closed_ids = [qid for qid in qids if q_by_id.get(qid) and q_by_id[qid].termen_limita < now]

            open_counts: dict[int, int] = {}
            if open_ids:
                rows = (
                    Submission.objects.filter(
                        status=Submission.STATUS_TRIMIS,
                        questionnaire_id__in=open_ids,
                        questionnaire__arhivat=False,
                        expert__is_active=True,
                        expert__is_staff=False,
                        expert__profil_expert__capitole=ch,
                    )
                    .values("questionnaire_id")
                    .annotate(cnt=Count("id", distinct=True))
                )
                open_counts = {r["questionnaire_id"]: r["cnt"] for r in rows}

            scope_key = QuestionnaireScopeSnapshot.make_scope_key(
                QuestionnaireScopeSnapshot.SCOPE_CHAPTER, chapter_id=ch.id
            )
            snap_map: dict[int, QuestionnaireScopeSnapshot] = {
                s.questionnaire_id: s
                for s in QuestionnaireScopeSnapshot.objects.filter(
                    questionnaire_id__in=closed_ids,
                    scope_key=scope_key,
                )
            }
            for qid in closed_ids:
                if qid not in snap_map and q_by_id.get(qid):
                    snap_map[qid] = ensure_scope_snapshot(
                        q_by_id[qid],
                        scope=QuestionnaireScopeSnapshot.SCOPE_CHAPTER,
                        chapter=ch,
                    )

            total_responses = 0
            rates: list[float] = []

            for qid in open_ids:
                num = int(open_counts.get(qid, 0))
                total_responses += num
                den = nr_experti_ch
                rates.append(round((num / den) * 100, 1) if den else 0.0)

            for qid in closed_ids:
                snap = snap_map.get(qid)
                if snap:
                    total_responses += int(snap.nr_raspunsuri or 0)
                    rates.append(float(snap.rata or 0.0))
                else:
                    rates.append(0.0)

            chapter_rows.append(
                {
                    "obj": ch,
                    "nr_experti": nr_experti_ch,
                    "nr_chestionare": nr_chestionare_ch,
                    "nr_raspunsuri": total_responses,
                    "rata_medie_raspuns": _avg(rates),
                }
            )

        grouped_chapter_stats.append((cl, chapter_rows))

    # Rată globală de răspuns = media tuturor mediilor pe capitole.
    # (Capitole fără chestionare au rata 0.0 în calculele de mai sus.)
    all_chapter_rates: list[float] = [
        float(r.get("rata_medie_raspuns") or 0.0)
        for _cl, rows in grouped_chapter_stats
        for r in rows
    ]
    rata_globala = round(sum(all_chapter_rates) / len(all_chapter_rates), 1) if all_chapter_rates else 0.0

    return render(
        request,
        "portal/admin_dashboard.html",
        {
            "chestionare": chestionare,
            "nr_experti": nr_experti,
            "nr_chestionare_total": nr_chestionare_total,
            "rata_globala": rata_globala,
            "criterii_stats": criterii_stats,
            "grouped_chapter_stats": grouped_chapter_stats,
        },
    )


@user_passes_test(is_admin)
def admin_newsletter_list(request):
    newsletters = Newsletter.objects.all().order_by("-creat_la")
    return render(request, "portal/admin_newsletters_list.html", {"newsletters": newsletters})


@user_passes_test(is_admin)
def admin_newsletter_create(request):
    if request.method == "POST":
        form = NewsletterForm(request.POST)
        if form.is_valid():
            nl = form.save(commit=False)
            nl.creat_de = request.user
            nl.save()
            messages.success(request, "Newsletterul a fost creat.")
            return redirect("admin_newsletter_edit", pk=nl.pk)
    else:
        form = NewsletterForm()

    return render(request, "portal/admin_newsletter_form.html", {"form": form, "titlu_pagina": "Newsletter nou"})


@user_passes_test(is_admin)
def admin_newsletter_edit(request, pk: int):
    nl = get_object_or_404(Newsletter, pk=pk)
    if request.method == "POST":
        form = NewsletterForm(request.POST, instance=nl)
        if form.is_valid():
            nl = form.save(commit=False)
            nl.save()
            if nl.este_trimis:
                messages.success(
                    request,
                    "Newsletterul a fost actualizat. Notă: emailurile deja trimise nu se modifică; se actualizează doar vizualizarea în platformă.",
                )
            else:
                messages.success(request, "Newsletterul a fost actualizat.")
            return redirect("admin_newsletter_edit", pk=pk)
    else:
        form = NewsletterForm(instance=nl)

    nr_destinatari = User.objects.filter(is_staff=False, is_active=True).exclude(email="").count()
    return render(
        request,
        "portal/admin_newsletter_form.html",
        {
            "form": form,
            "nl": nl,
            "titlu_pagina": "Editare newsletter",
            "nr_destinatari": nr_destinatari,
        },
    )


@user_passes_test(is_admin)
def admin_newsletter_send(request, pk: int):
    nl = get_object_or_404(Newsletter, pk=pk)
    nr_destinatari = User.objects.filter(is_staff=False, is_active=True).exclude(email="").count()

    if nl.este_trimis:
        messages.info(request, "Newsletterul a fost deja trimis.")
        return redirect("admin_newsletter_edit", pk=pk)

    if request.method == "POST":
        # confirmare dublă
        if request.POST.get("confirm") != "da":
            messages.error(request, "Bifează confirmarea pentru a trimite newsletterul.")
            return redirect("admin_newsletter_send", pk=pk)

        base_url = request.build_absolute_uri("/").rstrip("/")
        ok, fail = send_newsletter_emails(nl, request_base_url=base_url)
        nl.trimis_la = timezone.now()
        nl.trimis_de = request.user
        nl.nr_destinatari = nr_destinatari
        nl.nr_trimise = ok
        nl.nr_esecuri = fail
        nl.save(update_fields=["trimis_la", "trimis_de", "nr_destinatari", "nr_trimise", "nr_esecuri"])

        if ok and not fail:
            messages.success(request, f"Newsletter trimis cu succes către {ok} experți.")
        elif ok and fail:
            messages.warning(request, f"Newsletter trimis către {ok} experți. Eșecuri: {fail}.")
        else:
            messages.error(request, "Trimiterea newsletterului a eșuat. Verifică setările email.")

        return redirect("admin_newsletter_edit", pk=pk)

    return render(
        request,
        "portal/admin_newsletter_send_confirm.html",
        {"nl": nl, "nr_destinatari": nr_destinatari},
    )

@user_passes_test(is_internal)
def admin_questionnaire_list(request):
    # Număr de răspunsuri = doar submisiile TRIMIS (nu includem ciornele)
    chestionare = (
        Questionnaire.objects.filter(arhivat=False)
        .annotate(
            nr_raspunsuri=Count(
                "submisii",
                filter=Q(submisii__status=Submission.STATUS_TRIMIS),
                distinct=True,
            )
        )
        .order_by("-creat_la")
    )
    return render(request, "portal/admin_chestionare_list.html", {"chestionare": chestionare})

@user_passes_test(is_admin)
def admin_questionnaire_create(request):
    if request.method == "POST":
        form = ChestionarForm(request.POST)
        if form.is_valid():
            chestionar = form.save(user=request.user)

            # Trimite notificări pe email către experții relevanți (General -> toți; altfel după capitole/criterii)
            base_url = request.build_absolute_uri("/").rstrip("/")
            ok, fail = send_new_questionnaire_emails(chestionar, request_base_url=base_url)

            if ok and not fail:
                messages.success(request, f"Chestionarul a fost creat. Notificări trimise: {ok}.")
            elif ok and fail:
                messages.warning(
                    request,
                    f"Chestionarul a fost creat. Notificări trimise: {ok}. Eșecuri: {fail} (verifică setările email).")
            elif fail:
                messages.warning(
                    request,
                    f"Chestionarul a fost creat, dar trimiterea notificărilor a eșuat (Eșecuri: {fail}). ")
            else:
                messages.success(request, "Chestionarul a fost creat.")
            return redirect("admin_chestionar_edit", pk=chestionar.pk)
    else:
        form = ChestionarForm()

    question_fields = [form[f"intrebare_{i}"] for i in range(1, 21)]

    return render(
        request,
        "portal/admin_chestionar_form.html",
        {"form": form, "titlu_pagina": "Chestionar nou", "question_fields": question_fields},
    )


@user_passes_test(is_internal)
def admin_questionnaire_edit(request, pk: int):
    chestionar = get_object_or_404(Questionnaire, pk=pk)

    can_edit = is_admin(request.user)

    # Staff = doar vizualizare (fără editare).
    if request.method == "POST" and not can_edit:
        raise PermissionDenied

    if request.method == "POST":
        form = ChestionarForm(request.POST, instance=chestionar)
        if form.is_valid():
            form.save(user=request.user)
            messages.success(request, "Chestionarul a fost actualizat.")
            return redirect("admin_chestionar_edit", pk=pk)
    else:
        form = ChestionarForm(instance=chestionar)

    if not can_edit:
        for f in form.fields.values():
            f.disabled = True

    question_fields = [form[f"intrebare_{i}"] for i in range(1, 21)]

    # Număr de răspunsuri = doar cele TRIMIS (nu includem ciornele).
    trimise = chestionar.submisii.filter(status=Submission.STATUS_TRIMIS).count()
    ciorne = chestionar.submisii.filter(status=Submission.STATUS_DRAFT).count()
    total_raspunsuri = trimise

    return render(
        request,
        "portal/admin_chestionar_form.html",
        {
            "form": form,
            "chestionar": chestionar,
            "titlu_pagina": "Editare chestionar" if can_edit else "Detalii chestionar",
            "can_edit": can_edit,
            "total_raspunsuri": total_raspunsuri,
            "ciorne": ciorne,
            "trimise": trimise,
            "question_fields": question_fields,
        },
    )


@user_passes_test(is_internal)
def admin_expert_list(request):
    experti = User.objects.filter(is_staff=False, is_active=True).order_by("last_name", "first_name")
    return render(request, "portal/admin_experti_list.html", {"experti": experti})


# -------------------- STAFF users (administrare) --------------------


@user_passes_test(is_admin)
def admin_staff_list(request):
    """Listă utilizatori de tip Staff (doar admin)."""
    staff_users = (
        User.objects.filter(is_staff=True, is_superuser=False)
        .order_by("last_name", "first_name", "username")
    )
    return render(request, "portal/admin_staff_list.html", {"staff_users": staff_users})


@user_passes_test(is_admin)
def admin_staff_create(request):
    """Creează utilizator Staff (doar admin)."""
    if request.method == "POST":
        form = StaffCreateForm(request.POST)
        if form.is_valid():
            user, parola_generata = form.save()
            messages.success(request, f"Utilizatorul Staff a fost creat. Parolă: {parola_generata}")
            return redirect("admin_staff_list")
    else:
        form = StaffCreateForm()

    return render(
        request,
        "portal/admin_staff_form.html",
        {"form": form, "titlu_pagina": "Staff nou"},
    )


@user_passes_test(is_admin)
def admin_staff_edit(request, pk: int):
    """Editează un utilizator Staff (doar admin)."""
    staff_user = get_object_or_404(User, pk=pk, is_staff=True, is_superuser=False)

    if request.method == "POST":
        form = StaffUpdateForm(request.POST, user=staff_user)
        if form.is_valid():
            form.save()
            messages.success(request, "Utilizatorul Staff a fost actualizat.")
            return redirect("admin_staff_list")
    else:
        form = StaffUpdateForm(user=staff_user)

    return render(
        request,
        "portal/admin_staff_edit.html",
        {
            "form": form,
            "staff_user": staff_user,
            "titlu_pagina": "Editare Staff",
        },
    )


# -------------------- IMPORT experți (CSV) --------------------


@user_passes_test(is_admin)
def admin_expert_import_template(request):
    """Descarcă șablon CSV pentru import experți."""
    content = (
        "email,prenume,nume,telefon,organizatie,functie,sumar_expertiza,capitole,foi_de_parcurs\n"
        "ana.popa@example.com,Ana,Popa,+37369123456,Parlament,Consilier,achiziții publice și concurență,5;8,FID;RAP\n"
    )
    resp = HttpResponse(content, content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="template_import_experti.csv"'
    return resp


@user_passes_test(is_admin)
def admin_questionnaire_import_template(request):
    """Descarcă șablon CSV pentru import chestionare."""
    headers = [
        "id",
        "titlu",
        "descriere",
        "termen_limita",
        "este_general",
        "capitole",
        "foi_de_parcurs",
    ] + [f"intrebare_{i}" for i in range(1, 21)]

    example_row = [
        "",  # id (opțional)
        "Chestionar exemplu (General)",
        "Completează răspunsuri scurte și concrete.",
        "2026-02-15 23:59",
        "da",
        "",
        "",
    ] + [
        "Care sunt principalele riscuri de implementare?",
        "Ce modificări legislative sunt necesare?",
    ] + ["" for _ in range(3, 21)]

    example_row2 = [
        "",
        "Chestionar exemplu (Cap. 23 + FID)",
        "Comentarii pe transpunere și implementare.",
        "15.02.2026 18:00",
        "nu",
        "23",
        "FID",
    ] + [
        "Care sunt principalele lacune în cadrul normativ existent?",
        "Ce instituții trebuie implicate în implementare?",
    ] + ["" for _ in range(3, 21)]

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerow(example_row)
    w.writerow(example_row2)

    resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="template_import_chestionare.csv"'
    return resp


def _parse_capitole(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return []
    # Permitem separatori ; , |
    raw = raw.replace("|", ";").replace(",", ";")
    nums = set()
    for token in [t.strip() for t in raw.split(";") if t.strip()]:
        m = re.search(r"(\d{1,2})", token)
        if not m:
            raise ValueError(f"Capitol invalid: '{token}'")
        nums.add(int(m.group(1)))
    chapters = list(Chapter.objects.filter(numar__in=sorted(nums)))
    found = set([c.numar for c in chapters])
    missing = sorted(list(nums - found))
    if missing:
        raise ValueError(f"Capitole inexistente: {', '.join(str(x) for x in missing)}")
    return chapters


def _parse_criterii(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return []
    raw = raw.replace("|", ";").replace(",", ";")
    codes = []
    for token in [t.strip() for t in raw.split(";") if t.strip()]:
        codes.append(token.upper())
    qs = list(Criterion.objects.filter(cod__in=codes))
    found = set([c.cod.upper() for c in qs])
    missing = [c for c in codes if c not in found]
    if missing:
        raise ValueError(f"Foi de parcurs inexistente: {', '.join(missing)}")
    # păstrăm ordinea din fișier
    by_code = {c.cod.upper(): c for c in qs}
    return [by_code[c] for c in codes]


def _parse_bool(raw: str) -> bool:
    raw = (raw or "").strip().lower()
    if not raw:
        return False
    return raw in {"1", "true", "t", "yes", "y", "da", "adevărat", "adevarat"}


def _parse_deadline(raw: str) -> timezone.datetime:
    """Parsează termenul limită din CSV.

    Formate acceptate (exemple):
      - 2026-02-15 23:59
      - 15.02.2026 23:59
      - 2026-02-15
      - 15.02.2026

    Dacă lipsește ora, folosim 23:59.
    """
    raw0 = (raw or "").strip()
    if not raw0:
        raise ValueError("Lipsește termen_limita")

    fmts = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d/%m/%Y",
    ]

    dt = None
    for fmt in fmts:
        try:
            dt = datetime.strptime(raw0, fmt)
            break
        except Exception:
            continue
    if dt is None:
        raise ValueError(
            "Format termen_limita invalid. Folosește de ex. 2026-02-15 23:59 sau 15.02.2026 23:59."
        )

    # Dacă e doar data, setăm 23:59
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw0) or re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", raw0) or re.fullmatch(r"\d{2}/\d{2}/\d{4}", raw0):
        dt = dt.replace(hour=23, minute=59, second=0)

    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


# -------------------- PNA helpers --------------------


_RO_MONTHS = {
    "ianuarie": 1,
    "februarie": 2,
    "martie": 3,
    "aprilie": 4,
    "mai": 5,
    "iunie": 6,
    "iulie": 7,
    "august": 8,
    "septembrie": 9,
    "octombrie": 10,
    "noiembrie": 11,
    "decembrie": 12,
}


def _to_date_from_pna_term(raw_value, fallback_year=None):
    """Convertește termenul din Excel PNA în `date` (ziua=1).

    Acceptă:
      - datetime/date (openpyxl)
      - string "Aprilie 2026"
      - string "Iulie" + fallback_year
      - string "2026-08-01" etc.
    """
    if raw_value is None:
        return None

    # Datetime / date (openpyxl)
    try:
        import datetime as _dt

        if isinstance(raw_value, _dt.datetime):
            return raw_value.date().replace(day=1)
        if isinstance(raw_value, _dt.date):
            return raw_value.replace(day=1)
    except Exception:
        pass

    s = str(raw_value).strip()
    if not s:
        return None

    # Dacă e format ISO date
    for fmt in ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"]:
        try:
            d = datetime.strptime(s, fmt).date()
            return d.replace(day=1)
        except Exception:
            pass

    # "Aprilie 2026" / "Octombrie 2026" etc.
    m = re.match(r"^([A-Za-zăâîșțĂÂÎȘȚ]+)\s+(\d{4})$", s)
    if m:
        mon = m.group(1).strip().lower()
        year = int(m.group(2))
        if mon in _RO_MONTHS:
            return datetime(year, _RO_MONTHS[mon], 1).date()

    # Doar luna (folosim fallback_year)
    mon2 = s.lower()
    if mon2 in _RO_MONTHS and fallback_year:
        return datetime(int(fallback_year), _RO_MONTHS[mon2], 1).date()

    return None


def _parse_chapter_from_label(label: str):
    """Extrage numărul capitolului dintr-un text de tip "Capitolul 10 – ..."."""
    if not label:
        return None
    m = re.search(r"Capitolul\s*(\d{1,2})", str(label))
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _parse_primary_criterion_code(raw: str):
    """Extrage un cod scurt de foaie de parcurs din celula PNA.

    Exemplu valori în fișier: "RoL", "PAR", "CR - Criteriu de referință", "GP - Planul ...", combinații separate prin virgulă.
    În etapa 1, proiectul este atașat la un singur criteriu (cel mai relevant / primul detectat).
    """
    s = (raw or "").strip()
    if not s:
        return None

    s_up = s.upper()
    # ordine: dacă sunt combinate, încercăm să detectăm explicit coduri cunoscute
    for code in ["ROL", "PAR", "FDI", "GP", "CR"]:
        if re.search(rf"\b{code}\b", s_up):
            return "RoL" if code == "ROL" else code

    # fallback: primul token înainte de spațiu / virgulă / "-"
    token = re.split(r"[\s,–\-]+", s.strip(), maxsplit=1)[0]
    token = token.strip().upper()
    if not token:
        return None
    # păstrăm cazul preferat pentru RoL
    return "RoL" if token == "ROL" else token[:10]


def _extract_celex_from_link_or_code(raw: str) -> tuple[str, str]:
    """Extrage codul CELEX dintr-un link EUR-Lex sau dintr-un cod introdus manual.

    Returnează (celex, url).
    - `url` este păstrat doar dacă input-ul arată ca un URL.
    """
    s = (raw or "").strip()
    if not s:
        return "", ""

    # păstrăm URL dacă e URL
    url = s if s.lower().startswith("http") else ""

    # încercăm să găsim CELEX:xxxxx în string
    m = re.search(r"CELEX:([0-9A-Za-z]+)", s, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip(), url

    # dacă e parametru uri=CELEX:...
    m = re.search(r"uri=CELEX:([0-9A-Za-z]+)", s, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip(), url

    # altfel tratăm ca "cod" (și curățăm prefixul)
    celex = s.replace("CELEX:", "").replace("celex:", "").strip()
    # lăsăm doar caracterele alfanumerice (ca protecție)
    celex = re.sub(r"[^0-9A-Za-z]", "", celex)
    return celex, url


def _norm_inst_name(s: str) -> str:
    s0 = (s or "").strip()
    s0 = s0.replace("—", "-").replace("–", "-")
    s0 = re.sub(r"\s+", " ", s0)
    return s0.lower()


@user_passes_test(is_admin)
def admin_expert_import(request):
    """Importă experți din CSV.

    - Cheia unică: email
    - Duplicate: se actualizează (update)
    - Parole: se generează doar pentru utilizatorii noi (opțiunea A)
    """

    if request.method == "POST":
        form = ExpertImportCSVForm(request.POST, request.FILES)
        if form.is_valid():
            f = form.cleaned_data["fisier"]
            filename = getattr(f, 'name', '') or ''

            try:
                raw_bytes = f.read()
                text_csv = raw_bytes.decode("utf-8-sig")
            except Exception:
                messages.error(request, "Fișierul nu poate fi citit. Te rog salvează-l ca CSV UTF-8 și reîncearcă.")
                return redirect("admin_expert_import")

            reader = csv.DictReader(io.StringIO(text_csv))
            required = {"email", "prenume", "nume"}
            headers = set([h.strip() for h in (reader.fieldnames or [])])
            if not required.issubset(headers):
                messages.error(
                    request,
                    "Lipsesc coloane obligatorii. Fișierul trebuie să conțină cel puțin: email, prenume, nume.",
                )
                return redirect("admin_expert_import")

            report_rows = []
            cred_rows = []
            nr_create = nr_update = nr_error = 0

            for idx, row in enumerate(reader, start=2):
                email = (row.get("email") or "").strip().lower()
                prenume = (row.get("prenume") or "").strip()
                nume = (row.get("nume") or "").strip()

                if not email:
                    nr_error += 1
                    report_rows.append((idx, "", "ERROR", "Lipsește email"))
                    continue
                if not prenume or not nume:
                    nr_error += 1
                    report_rows.append((idx, email, "ERROR", "Lipsește prenume sau nume"))
                    continue

                telefon = (row.get("telefon") or "").strip()
                organizatie = (row.get("organizatie") or "").strip()
                functie = (row.get("functie") or "").strip()
                sumar = (row.get("sumar_expertiza") or "").strip()
                raw_caps = (row.get("capitole") or "").strip()
                raw_cr = (row.get("foi_de_parcurs") or row.get("criterii") or "").strip()

                try:
                    capitole = _parse_capitole(raw_caps)
                    criterii = _parse_criterii(raw_cr)
                except Exception as e:
                    nr_error += 1
                    report_rows.append((idx, email, "ERROR", str(e)))
                    continue

                # găsim utilizator existent
                existing = (
                    User.objects.filter(username=email).first()
                    or User.objects.filter(email=email).first()
                )

                try:
                    with transaction.atomic():
                        if existing:
                            if existing.is_staff:
                                raise ValueError("Email-ul aparține unui administrator; rândul a fost ignorat.")

                            existing.username = email
                            existing.email = email
                            existing.first_name = prenume
                            existing.last_name = nume
                            existing.is_staff = False
                            existing.is_active = True
                            existing.save()

                            profil = _get_or_create_profile(existing)
                            profil.telefon = telefon
                            profil.organizatie = organizatie
                            profil.functie = functie
                            profil.sumar_expertiza = sumar
                            # dacă era arhivat, îl reactivăm
                            profil.arhivat = False
                            profil.arhivat_la = None
                            profil.save()
                            profil.capitole.set(capitole)
                            profil.criterii.set(criterii)

                            nr_update += 1
                            report_rows.append((idx, email, "UPDATED", "Actualizat"))

                        else:
                            parola = secrets.token_urlsafe(10)
                            user = User.objects.create_user(
                                username=email,
                                email=email,
                                password=parola,
                                first_name=prenume,
                                last_name=nume,
                            )
                            user.is_staff = False
                            user.is_active = True
                            user.save()

                            profil = _get_or_create_profile(user)
                            profil.telefon = telefon
                            profil.organizatie = organizatie
                            profil.functie = functie
                            profil.sumar_expertiza = sumar
                            profil.arhivat = False
                            profil.arhivat_la = None
                            profil.save()
                            profil.capitole.set(capitole)
                            profil.criterii.set(criterii)

                            nr_create += 1
                            cred_rows.append((email, parola))
                            report_rows.append((idx, email, "CREATED", "Creat"))

                except Exception as e:
                    nr_error += 1
                    report_rows.append((idx, email, "ERROR", str(e)))

            # Construim CSV-urile pentru download
            rep_buf = io.StringIO()
            rep_w = csv.writer(rep_buf)
            rep_w.writerow(["rand", "email", "status", "mesaj"])
            rep_w.writerows(report_rows)

            cred_buf = io.StringIO()
            cred_w = csv.writer(cred_buf)
            cred_w.writerow(["email", "parola_temporara"])
            cred_w.writerows(cred_rows)

            run = ImportRun.objects.create(
                kind=ImportRun.KIND_EXPERTI,
                creat_de=request.user,
                nume_fisier=filename,
                nr_create=nr_create,
                nr_actualizate=nr_update,
                nr_erori=nr_error,
                raport_csv=rep_buf.getvalue(),
                cred_csv=cred_buf.getvalue() if cred_rows else "",
            )

            messages.success(
                request,
                f"Import finalizat. Creați: {nr_create}, Actualizați: {nr_update}, Erori: {nr_error}.",
            )
            return redirect("admin_import_run_detail", pk=run.pk)

    else:
        form = ExpertImportCSVForm()

    return render(
        request,
        "portal/admin_import_experti.html",
        {
            "form": form,
        },
    )


@user_passes_test(is_admin)
def admin_questionnaire_import(request):
    """Importă chestionare din CSV.

    - Cheia de update: id (opțional). Dacă id este completat și există, chestionarul se actualizează.
    - Dacă id lipsește: se creează chestionar nou.
    - Întrebări: intrebare_1...intrebare_20 (cel puțin una).
    - Pentru chestionarele noi: se trimit notificări email către experții relevanți.
    """

    if request.method == "POST":
        form = QuestionnaireImportCSVForm(request.POST, request.FILES)
        if form.is_valid():
            f = form.cleaned_data["fisier"]
            filename = getattr(f, "name", "") or ""

            try:
                raw_bytes = f.read()
                text_csv = raw_bytes.decode("utf-8-sig")
            except Exception:
                messages.error(request, "Fișierul nu poate fi citit. Te rog salvează-l ca CSV UTF-8 și reîncearcă.")
                return redirect("admin_questionnaire_import")

            reader = csv.DictReader(io.StringIO(text_csv))
            headers = set([h.strip() for h in (reader.fieldnames or []) if h])
            required = {"titlu", "termen_limita"}
            if not required.issubset(headers):
                messages.error(
                    request,
                    "Lipsesc coloane obligatorii. Fișierul trebuie să conțină cel puțin: titlu, termen_limita.",
                )
                return redirect("admin_questionnaire_import")

            # Cel puțin o coloană intrebare_1..20 trebuie să existe în antet
            has_q_cols = any([f"intrebare_{i}" in headers for i in range(1, 21)])
            if not has_q_cols:
                messages.error(
                    request,
                    "Lipsesc coloanele pentru întrebări. Adaugă cel puțin intrebare_1 (și până la intrebare_20).",
                )
                return redirect("admin_questionnaire_import")

            report_rows = []
            nr_create = nr_update = nr_error = 0

            # map instituții (pentru selectare + creare automată dacă lipsesc)
            inst_map = {_norm_inst_name(i.nume): i for i in PnaInstitution.objects.all()}

            def get_inst(name: str):
                name0 = (name or "").strip()
                if not name0:
                    return None
                key = _norm_inst_name(name0)
                obj = inst_map.get(key)
                if obj:
                    return obj
                obj = PnaInstitution.objects.create(nume=name0[:400])
                inst_map[key] = obj
                return obj

            def split_insts(raw: str):
                # separatori comuni: virgulă, ;, /, newline
                parts = [p.strip() for p in re.split(r"[;,/\n]+", raw or "") if p and str(p).strip()]
                return [p for p in parts if p]

            base_url = request.build_absolute_uri("/").rstrip("/")

            for idx, row in enumerate(reader, start=2):
                raw_id = (row.get("id") or "").strip()
                qid = None
                if raw_id:
                    try:
                        qid = int(raw_id)
                    except Exception:
                        nr_error += 1
                        report_rows.append((idx, raw_id, "ERROR", "ID invalid (nu este număr)"))
                        continue

                titlu = (row.get("titlu") or "").strip()
                descriere = (row.get("descriere") or "").strip()
                raw_deadline = (row.get("termen_limita") or "").strip()
                raw_general = (row.get("este_general") or "").strip()
                raw_caps = (row.get("capitole") or "").strip()
                raw_cr = (row.get("foi_de_parcurs") or row.get("criterii") or "").strip()

                if not titlu:
                    nr_error += 1
                    report_rows.append((idx, raw_id or "", "ERROR", "Lipsește titlu"))
                    continue

                try:
                    termen = _parse_deadline(raw_deadline)
                except Exception as e:
                    nr_error += 1
                    report_rows.append((idx, raw_id or "", "ERROR", str(e)))
                    continue

                este_general = _parse_bool(raw_general)

                try:
                    capitole = [] if este_general else _parse_capitole(raw_caps)
                    criterii = [] if este_general else _parse_criterii(raw_cr)
                except Exception as e:
                    nr_error += 1
                    report_rows.append((idx, raw_id or "", "ERROR", str(e)))
                    continue

                if not este_general and not capitole and not criterii:
                    nr_error += 1
                    report_rows.append(
                        (idx, raw_id or "", "ERROR", "Chestionar ne-general: trebuie selectat cel puțin un capitol sau criteriu"),
                    )
                    continue

                # întrebări
                intrebari = []
                for i in range(1, 21):
                    text = (row.get(f"intrebare_{i}") or "").strip()
                    if text:
                        intrebari.append(text)

                if not intrebari:
                    nr_error += 1
                    report_rows.append((idx, raw_id or "", "ERROR", "Nu există întrebări (completează cel puțin intrebare_1)"))
                    continue

                try:
                    with transaction.atomic():
                        created = False
                        if qid is not None:
                            q = Questionnaire.objects.filter(pk=qid).first()
                            if not q:
                                raise ValueError(f"Nu există chestionar cu id={qid}")

                            q.titlu = titlu
                            q.descriere = descriere
                            q.termen_limita = termen
                            q.este_general = este_general
                            q.arhivat = False
                            q.arhivat_la = None
                            q.save()

                            if este_general:
                                q.capitole.clear()
                                q.criterii.clear()
                            else:
                                q.capitole.set(capitole)
                                q.criterii.set(criterii)

                            # Întrebări:
                            # - dacă NU există submisii: putem înlocui lista complet (număr/ordine);
                            # - dacă EXISTĂ submisii: permitem doar actualizarea textului (typo/clarificări),
                            #   păstrând numărul/ordinea, pentru a nu rupe legătura cu răspunsurile.
                            if not q.submisii.exists():
                                q.intrebari.all().delete()
                                for ord_no, t in enumerate(intrebari, start=1):
                                    Question.objects.create(questionnaire=q, ord=ord_no, text=t[:1000])
                                msg = "Actualizat (întrebări înlocuite)"
                            else:
                                existing_qs = list(q.intrebari.all().order_by("ord"))
                                if len(existing_qs) != len(intrebari):
                                    msg = (
                                        "Actualizat (întrebările nu au fost modificate – există răspunsuri și numărul de întrebări diferă)"
                                    )
                                else:
                                    for ord_no, t in enumerate(intrebari, start=1):
                                        qq = existing_qs[ord_no - 1]
                                        qq.text = t[:1000]
                                        qq.save(update_fields=["text"])
                                    msg = "Actualizat (întrebări actualizate)"

                            nr_update += 1
                            report_rows.append((idx, str(q.pk), "UPDATED", msg))

                        else:
                            q = Questionnaire.objects.create(
                                titlu=titlu,
                                descriere=descriere,
                                termen_limita=termen,
                                este_general=este_general,
                                creat_de=request.user,
                                arhivat=False,
                            )

                            if este_general:
                                # păstrăm gol
                                pass
                            else:
                                q.capitole.set(capitole)
                                q.criterii.set(criterii)

                            for ord_no, t in enumerate(intrebari, start=1):
                                Question.objects.create(questionnaire=q, ord=ord_no, text=t)

                            created = True
                            nr_create += 1

                            report_rows.append((idx, str(q.pk), "CREATED", "Creat"))

                    # Email notificări doar pentru chestionare noi (în afara tranzacției)
                    if created:
                        ok, fail = send_new_questionnaire_emails(q, request_base_url=base_url)
                        if ok and not fail:
                            report_rows.append((idx, str(q.pk), "EMAIL", f"Notificări trimise: {ok}"))
                        elif ok and fail:
                            report_rows.append((idx, str(q.pk), "EMAIL", f"Notificări trimise: {ok}; Eșecuri: {fail}"))
                        elif fail:
                            report_rows.append((idx, str(q.pk), "EMAIL", f"Eșecuri la notificare: {fail}"))

                except Exception as e:
                    nr_error += 1
                    report_rows.append((idx, raw_id or "", "ERROR", str(e)))

            # Raport CSV
            rep_buf = io.StringIO()
            rep_w = csv.writer(rep_buf)
            rep_w.writerow(["rand", "id_chestionar", "status", "mesaj"])
            rep_w.writerows(report_rows)

            run = ImportRun.objects.create(
                kind=ImportRun.KIND_CHESTIONARE,
                creat_de=request.user,
                nume_fisier=filename,
                nr_create=nr_create,
                nr_actualizate=nr_update,
                nr_erori=nr_error,
                raport_csv=rep_buf.getvalue(),
                cred_csv="",
            )

            messages.success(
                request,
                f"Import finalizat. Create: {nr_create}, Actualizate: {nr_update}, Erori: {nr_error}.",
            )
            return redirect("admin_import_run_detail", pk=run.pk)

    else:
        form = QuestionnaireImportCSVForm()

    return render(request, "portal/admin_import_chestionare.html", {"form": form})


@user_passes_test(is_admin)
def admin_import_run_detail(request, pk: int):
    run = get_object_or_404(ImportRun, pk=pk)

    # extragem erorile (max 30) pentru afișaj
    errors_preview = []
    if run.raport_csv:
        r = csv.DictReader(io.StringIO(run.raport_csv))
        for row in r:
            if (row.get("status") or "").upper() == "ERROR":
                errors_preview.append(row)
            if len(errors_preview) >= 30:
                break

    # UI labels în funcție de tipul importului
    if run.kind == ImportRun.KIND_EXPERTI:
        back_url = "admin_experti_list"
        back_label = "Înapoi la Experți"
        back_icon = "bi-people"
        new_url = "admin_expert_import"
        new_label = "Import nou"
        new_icon = "bi-upload"
        create_label = "Experți creați"
        update_label = "Experți actualizați"
        has_credentials = bool(run.cred_csv)
    elif run.kind == ImportRun.KIND_CHESTIONARE:
        back_url = "admin_chestionare_list"
        back_label = "Înapoi la Chestionare"
        back_icon = "bi-ui-checks-grid"
        new_url = "admin_questionnaire_import"
        new_label = "Import nou"
        new_icon = "bi-upload"
        create_label = "Chestionare create"
        update_label = "Chestionare actualizate"
        has_credentials = False
    elif run.kind == ImportRun.KIND_PNA:
        back_url = "admin_pna_list"
        back_label = "Înapoi la PNA"
        back_icon = "bi-journal-text"
        new_url = "admin_pna_import"
        new_label = "Import nou"
        new_icon = "bi-upload"
        create_label = "Proiecte create"
        update_label = "Proiecte actualizate"
        has_credentials = False
    else:
        back_url = "admin_dashboard"
        back_label = "Înapoi"
        back_icon = "bi-arrow-left"
        new_url = "admin_dashboard"
        new_label = "Panou"
        new_icon = "bi-speedometer2"
        create_label = "Înregistrări create"
        update_label = "Înregistrări actualizate"
        has_credentials = bool(run.cred_csv)

    return render(
        request,
        "portal/admin_import_run_detail.html",
        {
            "run": run,
            "errors_preview": errors_preview,
            "has_credentials": has_credentials,
            "back_url": back_url,
            "back_label": back_label,
            "back_icon": back_icon,
            "new_url": new_url,
            "new_label": new_label,
            "new_icon": new_icon,
            "create_label": create_label,
            "update_label": update_label,
        },
    )


@user_passes_test(is_admin)
def admin_import_run_report_csv(request, pk: int):
    run = get_object_or_404(ImportRun, pk=pk)
    resp = HttpResponse(run.raport_csv or "", content_type="text/csv; charset=utf-8")
    ts = run.creat_la.strftime("%Y%m%d_%H%M")
    kind_slug = (
        "experti"
        if run.kind == ImportRun.KIND_EXPERTI
        else (
            "chestionare"
            if run.kind == ImportRun.KIND_CHESTIONARE
            else ("pna" if run.kind == ImportRun.KIND_PNA else "import")
        )
    )
    resp["Content-Disposition"] = f'attachment; filename="raport_{kind_slug}_{ts}.csv"'
    return resp


@user_passes_test(is_admin)
def admin_import_run_credentials_csv(request, pk: int):
    run = get_object_or_404(ImportRun, pk=pk)
    resp = HttpResponse(run.cred_csv or "", content_type="text/csv; charset=utf-8")
    ts = run.creat_la.strftime("%Y%m%d_%H%M")
    resp["Content-Disposition"] = f'attachment; filename="credentiale_{ts}.csv"'
    return resp

@user_passes_test(is_admin)
def admin_expert_create(request):
    if request.method == "POST":
        form = ExpertCreateForm(request.POST)
        if form.is_valid():
            user, parola_generata = form.save()
            messages.success(request, f"Expertul a fost creat. Parolă: {parola_generata}")
            return redirect("admin_expert_edit", pk=user.pk)
    else:
        form = ExpertCreateForm()

    return render(
        request,
        "portal/admin_expert_form.html",
        {"form": form, "titlu_pagina": "Expert nou"},
    )


@user_passes_test(is_internal)
def admin_expert_edit(request, pk: int):
    user = get_object_or_404(User, pk=pk)
    if user.is_staff:
        messages.error(request, "Acest utilizator este intern (Administrator/Staff), nu expert.")
        return redirect("admin_experti_list")

    can_edit = is_admin(request.user)

    # Staff = doar vizualizare (fără editare).
    if request.method == "POST" and not can_edit:
        raise PermissionDenied

    if request.method == "POST":
        form = ExpertUpdateForm(request.POST, user=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profilul expertului a fost actualizat.")
            return redirect("admin_expert_edit", pk=pk)
    else:
        form = ExpertUpdateForm(user=user)

    if not can_edit:
        for f in form.fields.values():
            f.disabled = True

    profil = _get_or_create_profile(user)

    return render(
        request,
        "portal/admin_expert_form.html",
        {
            "form": form,
            "titlu_pagina": "Editare expert" if can_edit else "Detalii expert",
            "expert_user": user,
            "profil": profil,
            "can_edit": can_edit,
        },
    )


@user_passes_test(is_internal)
def admin_expert_dashboard(request, pk: int):
    """Dashboard per expert (Admin + Staff).

    Afișează statisticile contribuțiilor expertului:
      - chestionare (submisii trimise / ciorne)
      - proiecte PNA (comentarii pe 3 dimensiuni)
    """

    expert = get_object_or_404(User, pk=pk, is_staff=False)
    profil = _get_or_create_profile(expert)

    # -------------------- Chestionare --------------------
    chestionare_qs = _expert_accessible_qs(expert).order_by("-termen_limita", "titlu")
    chestionare = list(chestionare_qs)

    sub_qs = Submission.objects.filter(expert=expert, questionnaire__in=chestionare_qs)
    sub_by_q = {s.questionnaire_id: s for s in sub_qs}

    nr_total_chestionare = len(chestionare)
    nr_trimise = sum(1 for s in sub_by_q.values() if s.status == Submission.STATUS_TRIMIS)
    nr_ciorne = sum(1 for s in sub_by_q.values() if s.status == Submission.STATUS_DRAFT)
    nr_neincepute = nr_total_chestionare - nr_trimise - nr_ciorne

    q_rows = []
    for q in chestionare:
        s = sub_by_q.get(q.id)
        status = "neinceput"
        if s:
            status = "trimis" if s.status == Submission.STATUS_TRIMIS else "draft"
        raspuns_url = None
        if s and s.status == Submission.STATUS_TRIMIS:
            raspuns_url = reverse(
                "admin_chestionar_raspunsuri_expert",
                kwargs={"pk": q.pk, "expert_id": expert.pk},
            )
        q_rows.append({"q": q, "submission": s, "status": status, "raspuns_url": raspuns_url})

    # -------------------- PNA --------------------
    pna_qs = _expert_pna_accessible_qs(expert)
    proiecte = list(pna_qs)

    contribs = (
        PnaExpertContribution.objects.filter(expert=expert, project__in=pna_qs)
        .select_related("project")
        .order_by("-updated_at")
    )
    contrib_by_project = {c.project_id: c for c in contribs}

    nr_total_proiecte = len(proiecte)
    nr_cu_contrib = sum(1 for c in contribs if c.are_orice)

    p_rows = []
    for p in proiecte:
        c = contrib_by_project.get(p.id)
        flex = ((c.flexibilitate if c else "") or "").strip()
        comp = ((c.compensare if c else "") or "").strip()
        tran = ((c.tranzitie if c else "") or "").strip()
        p_rows.append(
            {
                "p": p,
                "contrib": c,
                "has_any": bool(flex or comp or tran),
                "has_flex": bool(flex),
                "has_comp": bool(comp),
                "has_tran": bool(tran),
                "detail_url": reverse(
                    "admin_pna_contributii_expert",
                    kwargs={"pk": p.pk, "expert_id": expert.pk},
                ),
            }
        )

    p_rows.sort(key=lambda r: (not r["has_any"], r["p"].titlu))

    # KPIs
    rata_chestionare = round((nr_trimise / nr_total_chestionare) * 100, 1) if nr_total_chestionare else 0.0
    rata_pna = round((nr_cu_contrib / nr_total_proiecte) * 100, 1) if nr_total_proiecte else 0.0

    return render(
        request,
        "portal/admin_expert_dashboard.html",
        {
            "expert": expert,
            "profil": profil,
            "nr_total_chestionare": nr_total_chestionare,
            "nr_trimise": nr_trimise,
            "nr_ciorne": nr_ciorne,
            "nr_neincepute": nr_neincepute,
            "rata_chestionare": rata_chestionare,
            "q_rows": q_rows,
            "nr_total_proiecte": nr_total_proiecte,
            "nr_cu_contrib": nr_cu_contrib,
            "rata_pna": rata_pna,
            "p_rows": p_rows,
        },
    )


@user_passes_test(is_internal)
def admin_referinte(request):
    grouped = group_chapters_by_cluster()
    criterii = Criterion.objects.all().order_by("cod")
    return render(
        request,
        "portal/admin_referinte.html",
        {"grouped": grouped, "criterii": criterii},
    )


# -------------------- PNA (admin) --------------------


@user_passes_test(is_internal)
def admin_pna_list(request):
    """Pagina PNA (admin): listă + tabel structurat pe capitole/foi de parcurs."""

    q = (request.GET.get("q") or "").strip()
    stage = (request.GET.get("stage") or "").strip()

    proiecte_qs = (
        PnaProject.objects.filter(arhivat=False)
        .select_related("chapter", "criterion", "institutie_principala_ref")
        .prefetch_related("acte_ue_legaturi__eu_act")
        .prefetch_related("institutii_responsabile")
        .order_by("titlu")
    )
    if q:
        proiecte_qs = proiecte_qs.filter(
            Q(titlu__icontains=q)
            | Q(institutie_principala__icontains=q)
            | Q(institutie_principala_ref__nume__icontains=q)
            | Q(institutii_responsabile__nume__icontains=q)
        ).distinct()

    all_projects = list(proiecte_qs)

    # Statistici rapide (dashboard)
    today = timezone.localdate()

    def _gov_overdue(p: PnaProject) -> bool:
        d = p.termen_guvern_efectiv
        return bool(d and d < today)

    def _parl_overdue(p: PnaProject) -> bool:
        d = p.termen_aprobare_parlament
        return bool(d and d < today)

    total = len(all_projects)
    nr_neinitiate = sum(1 for p in all_projects if p.status_implementare == PnaProject.STATUS_NEINITIAT)
    nr_in_procedura_guvern = sum(
        1
        for p in all_projects
        if p.status_implementare in {
            PnaProject.STATUS_INITIAT_GUVERN,
            PnaProject.STATUS_AVIZARE_GUVERN,
            PnaProject.STATUS_COORDONARE_CE,
            PnaProject.STATUS_APROBARE_GUVERN,
        }
    )
    nr_in_procedura_parlament = sum(
        1
        for p in all_projects
        if p.status_implementare in {
            PnaProject.STATUS_INITIAT_PARLAMENT,
            PnaProject.STATUS_AVIZARE_PARLAMENT,
        }
    )
    nr_adoptate_final = sum(1 for p in all_projects if p.status_implementare == PnaProject.STATUS_ADOPTAT_FINAL)

    # Upcoming (următoarele 60 zile) pe termenul "cel mai apropiat" dintre Guvern/Parlament
    def _next_deadline(p: PnaProject):
        cands = [d for d in [p.termen_guvern_efectiv, p.termen_aprobare_parlament] if d]
        return min(cands) if cands else None

    upcoming_60 = [p for p in all_projects if _next_deadline(p) and today <= _next_deadline(p) <= (today + timedelta(days=60))]
    nr_upcoming_60 = len(upcoming_60)

    proiecte_qs = _apply_pna_stage_filter_to_qs(proiecte_qs, stage)
    proiecte = list(proiecte_qs)

    # Grupare pe criterii (foi de parcurs)
    by_criterion = {}
    by_chapter = {}
    for p in proiecte:
        if p.criterion_id:
            by_criterion.setdefault(p.criterion_id, []).append(p)
        elif p.chapter_id:
            by_chapter.setdefault(p.chapter_id, []).append(p)

    criterii = list(Criterion.objects.all().order_by("cod"))
    criterii_groups = []
    for cr in criterii:
        rows = by_criterion.get(cr.id, [])
        if rows:
            # sortare: după termenul apropiat
            rows.sort(key=lambda p: (_next_deadline(p) or datetime.max.date(), p.titlu))
            criterii_groups.append({"criteriu": cr, "proiecte": rows})

    grouped_chapters = group_chapters_by_cluster()
    chapter_groups = []
    for cl, chapters in grouped_chapters:
        ch_rows = []
        for ch in chapters:
            rows = by_chapter.get(ch.id, [])
            if rows:
                rows.sort(key=lambda p: (_next_deadline(p) or datetime.max.date(), p.titlu))
                ch_rows.append({"capitol": ch, "proiecte": rows})
        if ch_rows:
            chapter_groups.append({"cluster": cl, "chapters": ch_rows})

    return render(
        request,
        "portal/admin_pna_list.html",
        {
            "q": q,
            "can_edit_pna": can_edit_pna(request.user),
            "total": total,
            "nr_neinitiate": nr_neinitiate,
            "nr_in_procedura_guvern": nr_in_procedura_guvern,
            "nr_in_procedura_parlament": nr_in_procedura_parlament,
            "nr_adoptate_final": nr_adoptate_final,
            "criterii_groups": criterii_groups,
            "chapter_groups": chapter_groups,
            "stage": stage,
        },
    )


@user_passes_test(is_internal)
def admin_pna_dashboard(request):
    """Dashboard PNA (admin) – monitorizare progres, termene, riscuri, resurse, costuri.

    Include:
      - total proiecte + distribuție pe status (nr + %)
      - KPI-uri operaționale (termene, avizare CE, expertiză, costuri, calitate date)
      - matrice: foi de parcurs + capitole vs luni (an selectat), cu mod: nr proiecte / volum muncă (zile)
      - liste: deadline-uri depășite / apropiate, top riscuri, derapaje, top instituții, acte UE.

    Deadline pentru matrice/termene: termen actualizat Guvern (dacă există), altfel termen Parlament,
    fallback: termen Guvern.
    """

    proiecte_qs = PnaProject.objects.filter(arhivat=False)

    return _render_pna_dashboard(
        request,
        proiecte_qs=proiecte_qs,
        scope_kind="global",
        scope_title=None,
        scope_filters={},
        back_url=reverse("admin_pna_list"),
        back_label="Înapoi la PNA",
    )


@user_passes_test(is_internal)
def admin_pna_dashboard_institution(request, pk: int):
    inst = get_object_or_404(PnaInstitution, pk=pk)

    include_co = (request.GET.get("include_co") or "").strip() == "1"
    if include_co:
        proiecte_qs = (
            PnaProject.objects.filter(arhivat=False)
            .filter(Q(institutie_principala_ref=inst) | Q(institutii_responsabile=inst))
            .distinct()
        )
    else:
        proiecte_qs = PnaProject.objects.filter(arhivat=False, institutie_principala_ref=inst)

    scope_filters = {"institution": inst.id}
    if include_co:
        scope_filters["include_co"] = 1

    return _render_pna_dashboard(
        request,
        proiecte_qs=proiecte_qs,
        scope_kind="institution",
        scope_title=inst.nume,
        scope_filters=scope_filters,
        back_url=reverse("admin_pna_institution_list"),
        back_label="Înapoi la instituții",
        scope_obj=inst,
    )


@user_passes_test(is_internal)
def admin_pna_dashboard_chapter(request, pk: int):
    ch = get_object_or_404(Chapter, pk=pk)
    proiecte_qs = PnaProject.objects.filter(arhivat=False, chapter=ch)
    return _render_pna_dashboard(
        request,
        proiecte_qs=proiecte_qs,
        scope_kind="chapter",
        scope_title=f"Cap. {ch.numar} — {ch.denumire}",
        scope_filters={"chapter": ch.id},
        back_url=reverse("admin_pna_list"),
        back_label="Înapoi la PNA",
        scope_obj=ch,
    )


@user_passes_test(is_internal)
def admin_pna_dashboard_criterion(request, pk: int):
    cr = get_object_or_404(Criterion, pk=pk)
    proiecte_qs = PnaProject.objects.filter(arhivat=False, criterion=cr)
    return _render_pna_dashboard(
        request,
        proiecte_qs=proiecte_qs,
        scope_kind="criterion",
        scope_title=f"{cr.cod} — {cr.denumire}",
        scope_filters={"criterion": cr.id},
        back_url=reverse("admin_pna_list"),
        back_label="Înapoi la PNA",
        scope_obj=cr,
    )


def _render_pna_dashboard(
    request,
    proiecte_qs,
    scope_kind: str,
    scope_title: str | None,
    scope_filters: dict,
    back_url: str,
    back_label: str,
    scope_obj=None,
):
    """Renderer comun pentru dashboard-ul PNA (global / per instituție / per capitol / per foaie de parcurs).

    scope_filters sunt parametrii (querystring) care trebuie păstrați pe link-urile de drill-down
    către lista filtrată (ex: {"institution": 3} sau {"chapter": 24}).
    """

    mode = (request.GET.get("mode") or "count").strip().lower()
    if mode not in {"count", "days"}:
        mode = "count"
    stage = (request.GET.get("stage") or "").strip()

    base_qs = proiecte_qs
    all_projects = list(
        base_qs.select_related("chapter", "criterion", "institutie_principala_ref")
        .prefetch_related("institutii_responsabile", "acte_ue_legaturi__eu_act")
        .order_by("-actualizat_la")
    )

    proiecte_qs = _apply_pna_stage_filter_to_qs(proiecte_qs, stage)
    proiecte = list(
        proiecte_qs.select_related("chapter", "criterion", "institutie_principala_ref")
        .prefetch_related("institutii_responsabile", "acte_ue_legaturi__eu_act")
        .order_by("-actualizat_la")
    )

    project_ids = [p.id for p in proiecte]

    # -------------------- contribuții experți (etapa 2) --------------------
    # IMPORTANT: există rânduri "goale" create de get_or_create atunci când un expert deschide proiectul.
    # De aceea, pentru progres numărăm DOAR contribuțiile unde există text în cel puțin una din cele 3 boxe.
    contrib_flags_by_project = {pid: {"f": False, "c": False, "t": False, "any": False, "contributors": 0} for pid in project_ids}

    if project_ids:
        q_f = ~Q(flexibilitate="")
        q_c = ~Q(compensare="")
        q_t = ~Q(tranzitie="")
        q_any = q_f | q_c | q_t

        contrib_agg = (
            PnaExpertContribution.objects.filter(project_id__in=project_ids)
            .values("project_id")
            .annotate(
                f_cnt=Count("id", filter=q_f),
                c_cnt=Count("id", filter=q_c),
                t_cnt=Count("id", filter=q_t),
                any_cnt=Count("id", filter=q_any),
                expert_cnt=Count("expert", distinct=True, filter=q_any),
            )
        )
        for r in contrib_agg:
            pid = r.get("project_id")
            if not pid:
                continue
            f = int(r.get("f_cnt") or 0)
            c = int(r.get("c_cnt") or 0)
            t = int(r.get("t_cnt") or 0)
            any_cnt = int(r.get("any_cnt") or 0)
            expert_cnt = int(r.get("expert_cnt") or 0)
            contrib_flags_by_project[pid] = {
                "f": f > 0,
                "c": c > 0,
                "t": t > 0,
                "any": any_cnt > 0,
                "contributors": expert_cnt,
            }

    # "Eligibili" = experți (nu staff/admin) alocați capitolului/foii de parcurs a proiectului.
    chapter_ids = sorted({p.chapter_id for p in proiecte if p.chapter_id})
    criterion_ids = sorted({p.criterion_id for p in proiecte if p.criterion_id})

    eligible_experts_by_chapter = {}
    eligible_experts_by_criterion = {}
    experts_base_qs = User.objects.filter(is_active=True, is_staff=False, is_superuser=False).filter(profil_expert__arhivat=False)
    if chapter_ids:
        rows = (
            experts_base_qs.filter(profil_expert__capitole__id__in=chapter_ids)
            .values("profil_expert__capitole")
            .annotate(nr=Count("id", distinct=True))
        )
        eligible_experts_by_chapter = {int(r["profil_expert__capitole"]): int(r["nr"]) for r in rows if r.get("profil_expert__capitole")}
    if criterion_ids:
        rows = (
            experts_base_qs.filter(profil_expert__criterii__id__in=criterion_ids)
            .values("profil_expert__criterii")
            .annotate(nr=Count("id", distinct=True))
        )
        eligible_experts_by_criterion = {int(r["profil_expert__criterii"]): int(r["nr"]) for r in rows if r.get("profil_expert__criterii")}

    def _eligible_experts_for_project(p: PnaProject) -> int:
        if p.chapter_id:
            return int(eligible_experts_by_chapter.get(p.chapter_id, 0))
        if p.criterion_id:
            return int(eligible_experts_by_criterion.get(p.criterion_id, 0))
        return 0

    # Parametri de scope (păstrați pe drill-down-uri)
    scope_filters = scope_filters or {}
    scope_params = urlencode(scope_filters)

    include_co = (request.GET.get("include_co") or "").strip() == "1"
    toggle_include_co_url = None
    if scope_kind == "institution":
        params = request.GET.copy()
        if include_co:
            try:
                params.pop("include_co")
            except Exception:
                pass
        else:
            params["include_co"] = "1"
        qs_toggle = params.urlencode()
        toggle_include_co_url = request.path + (f"?{qs_toggle}" if qs_toggle else "")

    def _filtered_list_url(extra: dict | None = None) -> str:
        params = dict(scope_filters)
        if extra:
            for k, v in extra.items():
                if v is None or v == "":
                    continue
                params[k] = v
        qs = urlencode(params)
        return reverse("admin_pna_filtered_list") + (f"?{qs}" if qs else "")

    total = len(all_projects)
    today = timezone.localdate()

    # -------------------- KPI header dashboard --------------------
    nr_neinitiate = sum(1 for p in all_projects if p.status_implementare == PnaProject.STATUS_NEINITIAT)
    statusuri_guvern = {
        PnaProject.STATUS_INITIAT_GUVERN,
        PnaProject.STATUS_AVIZARE_GUVERN,
        PnaProject.STATUS_COORDONARE_CE,
        PnaProject.STATUS_APROBARE_GUVERN,
    }
    nr_in_procedura_guvern = sum(1 for p in all_projects if p.status_implementare in statusuri_guvern)
    statusuri_parlament = {
        PnaProject.STATUS_INITIAT_PARLAMENT,
        PnaProject.STATUS_AVIZARE_PARLAMENT,
    }
    nr_in_procedura_parlament = sum(1 for p in all_projects if p.status_implementare in statusuri_parlament)
    nr_adoptate_final = sum(1 for p in all_projects if p.status_implementare == PnaProject.STATUS_ADOPTAT_FINAL)

    # -------------------- progres contribuții experți --------------------
    # La nivel de proiect: considerăm "completat" dacă există cel puțin o contribuție non-goală.
    nr_contrib_any = 0
    nr_contrib_f = 0
    nr_contrib_c = 0
    nr_contrib_t = 0
    nr_contrib_all = 0

    contrib_dims_dist = {0: 0, 1: 0, 2: 0, 3: 0}

    for p in proiecte:
        flags = contrib_flags_by_project.get(p.id) or {}
        has_f = bool(flags.get("f"))
        has_c = bool(flags.get("c"))
        has_t = bool(flags.get("t"))
        has_any = bool(flags.get("any"))

        if has_any:
            nr_contrib_any += 1
        if has_f:
            nr_contrib_f += 1
        if has_c:
            nr_contrib_c += 1
        if has_t:
            nr_contrib_t += 1
        if has_f and has_c and has_t:
            nr_contrib_all += 1

        dims = int(has_f) + int(has_c) + int(has_t)
        contrib_dims_dist[dims] = contrib_dims_dist.get(dims, 0) + 1

    def _pct(n: int) -> float:
        return round((n / total) * 100, 1) if total else 0.0

    # Ultimele contribuții ale experților (max 20) – pentru dashboard
    latest_contribution_rows = []
    latest_contrib_qs = (
        PnaExpertContribution.objects.filter(project_id__in=project_ids)
        .select_related("project", "expert", "expert__profil_expert")
        .order_by("-updated_at")
    )
    for c in latest_contrib_qs:
        if not c.are_orice:
            continue
        latest_contribution_rows.append(
            {
                "project": c.project,
                "expert": c.expert,
                "profil": getattr(c.expert, "profil_expert", None),
                "contrib": c,
                "has_flex": bool((c.flexibilitate or "").strip()),
                "has_comp": bool((c.compensare or "").strip()),
                "has_tran": bool((c.tranzitie or "").strip()),
            }
        )
        if len(latest_contribution_rows) >= 20:
            break

    all_contributions_url = reverse("admin_pna_all_contributions")
    if scope_params:
        all_contributions_url += f"?{scope_params}"

    contrib_summary_cards = [
        {
            "key": "any",
            "label": "Proiecte cu contribuții (oricare)",
            "nr": nr_contrib_any,
            "pct": _pct(nr_contrib_any),
            "href": _filtered_list_url({"has_contrib": 1}),
        },
        {
            "key": "f",
            "label": "Flexibilitate completată",
            "nr": nr_contrib_f,
            "pct": _pct(nr_contrib_f),
            "href": _filtered_list_url({"missing_flex": 1}),
            "href_mode": "missing",
        },
        {
            "key": "c",
            "label": "Compensare completată",
            "nr": nr_contrib_c,
            "pct": _pct(nr_contrib_c),
            "href": _filtered_list_url({"missing_comp": 1}),
            "href_mode": "missing",
        },
        {
            "key": "t",
            "label": "Tranziție completată",
            "nr": nr_contrib_t,
            "pct": _pct(nr_contrib_t),
            "href": _filtered_list_url({"missing_tran": 1}),
            "href_mode": "missing",
        },
        {
            "key": "all",
            "label": "Toate 3 dimensiuni completate",
            "nr": nr_contrib_all,
            "pct": _pct(nr_contrib_all),
            "href": _filtered_list_url({"missing_all_dims": 1}),
            "href_mode": "missing",
        },
    ]

    # Distribuție pe nr. dimensiuni completate (0/1/2/3) – pentru o mini-diagramă.
    contrib_dims_rows = []
    max_dims_nr = max(contrib_dims_dist.values()) if contrib_dims_dist else 0
    for k in [0, 1, 2, 3]:
        nr = int(contrib_dims_dist.get(k, 0))
        contrib_dims_rows.append(
            {
                "k": k,
                "label": f"{k} / 3",
                "nr": nr,
                "pct": _pct(nr),
                "bar_h": round((nr / max_dims_nr) * 100, 1) if max_dims_nr else 0.0,
            }
        )

    # Top proiecte fără contribuții (pentru prioritizare)
    missing_contrib_projects = [p for p in proiecte if not (contrib_flags_by_project.get(p.id) or {}).get("any")]
    missing_contrib_projects.sort(key=lambda p: (p.termen_deadline or datetime.max.date(), -(p.prioritate or 0), p.titlu or ""))
    contrib_missing_top = [
        {
            "project": p,
            "deadline": p.termen_deadline,
            "eligible": _eligible_experts_for_project(p),
        }
        for p in missing_contrib_projects[:20]
    ]

    contrib_missing_url = _filtered_list_url({"missing_contrib": 1})

    # Breakdown: capitol / foaie de parcurs / instituție
    def _init_cs():
        return {"total": 0, "any": 0, "f": 0, "c": 0, "t": 0, "all": 0}

    cs_chapters = {}
    cs_criterii = {}
    cs_institutions = {}

    for p in proiecte:
        flags = contrib_flags_by_project.get(p.id) or {}
        has_f = bool(flags.get("f"))
        has_c = bool(flags.get("c"))
        has_t = bool(flags.get("t"))
        has_any = bool(flags.get("any"))
        has_all = bool(has_f and has_c and has_t)

        # capitol/foaie
        if p.chapter_id:
            s = cs_chapters.setdefault(p.chapter_id, _init_cs())
        else:
            s = cs_criterii.setdefault(p.criterion_id, _init_cs())
        s["total"] += 1
        if has_any:
            s["any"] += 1
        if has_f:
            s["f"] += 1
        if has_c:
            s["c"] += 1
        if has_t:
            s["t"] += 1
        if has_all:
            s["all"] += 1

        # instituție principală
        inst_id = int(p.institutie_principala_ref_id or 0)
        s2 = cs_institutions.setdefault(inst_id, _init_cs())
        s2["total"] += 1
        if has_any:
            s2["any"] += 1
        if has_f:
            s2["f"] += 1
        if has_c:
            s2["c"] += 1
        if has_t:
            s2["t"] += 1
        if has_all:
            s2["all"] += 1

    def _finalize_cs(s: dict) -> dict:
        t = int(s.get("total") or 0)
        out = dict(s)
        out["missing"] = t - int(out.get("any") or 0)
        for k in ["any", "f", "c", "t", "all"]:
            out[f"{k}_pct"] = round((int(out.get(k) or 0) / t) * 100, 1) if t else 0.0
        return out

    # Capitole (grupate pe clustere)
    contrib_chapter_groups = []
    for cl, chapters in group_chapters_by_cluster():
        rows = []
        for ch in chapters:
            if ch.id not in cs_chapters:
                continue
            rows.append(
                {
                    "obj": ch,
                    "stats": _finalize_cs(cs_chapters[ch.id]),
                    "dashboard_url": reverse("admin_pna_dashboard_chapter", kwargs={"pk": ch.id}),
                    "filter_url": _filtered_list_url({"chapter": ch.id}),
                }
            )
        if rows:
            contrib_chapter_groups.append({"cluster": cl, "rows": rows})

    # Foi de parcurs
    contrib_criterii_rows = []
    for cr in Criterion.objects.all().order_by("cod"):
        if cr.id not in cs_criterii:
            continue
        contrib_criterii_rows.append(
            {
                "obj": cr,
                "stats": _finalize_cs(cs_criterii[cr.id]),
                "dashboard_url": reverse("admin_pna_dashboard_criterion", kwargs={"pk": cr.id}),
                "filter_url": _filtered_list_url({"criterion": cr.id}),
            }
        )

    # Instituții (principal)
    inst_ids = [i for i in cs_institutions.keys() if i]
    inst_lookup = {i.id: i for i in PnaInstitution.objects.filter(id__in=inst_ids)} if inst_ids else {}
    contrib_institution_rows = []
    for inst_id, s in cs_institutions.items():
        inst_obj = inst_lookup.get(inst_id) if inst_id else None
        name = inst_obj.nume if inst_obj else "(nespecificat)"
        contrib_institution_rows.append(
            {
                "id": inst_id or None,
                "name": name,
                "obj": inst_obj,
                "stats": _finalize_cs(s),
                "dashboard_url": reverse("admin_pna_dashboard_institution", kwargs={"pk": inst_id}) if inst_id else None,
                "filter_url": _filtered_list_url({"institution": inst_id}) if inst_id else _filtered_list_url({"missing_institution": 1}),
            }
        )
    contrib_institution_rows.sort(key=lambda r: (int(r["stats"].get("total") or 0), r["name"]), reverse=True)
    contrib_institution_rows = contrib_institution_rows[:30]

    # Prag pentru "stagnare" (zile în același status fără schimbare)
    try:
        stale_days_threshold = int(request.GET.get("stale_days") or 60)
    except Exception:
        stale_days_threshold = 60
    stale_days_threshold = max(1, min(stale_days_threshold, 3650))

    # -------------------- istoric status (etapa 2) --------------------
    # Ultima schimbare de status per proiect + activitate pe luni
    from django.db.models import Max
    last_status_by_project = {}
    if proiecte:
        # max(changed_at) per proiect
        rows = (
            PnaProjectStatusHistory.objects.filter(project_id__in=[p.id for p in proiecte])
            .values("project_id")
            .annotate(last_dt=Max("changed_at"))
        )
        last_status_by_project = {r["project_id"]: r["last_dt"] for r in rows if r.get("last_dt")}

    # În lipsa istoricului (nu ar trebui după migrare), fallback: creat_la
    def _status_since(p: PnaProject):
        dt = last_status_by_project.get(p.id)
        if dt:
            return dt
        return getattr(p, "creat_la", None) or getattr(p, "actualizat_la", None)

    # Proiecte stagnante (non-finale)
    stale_projects = []
    for p in proiecte:
        since_dt = _status_since(p)
        if not since_dt:
            continue
        days = (today - since_dt.date()).days
        if days >= stale_days_threshold and p.status_implementare != PnaProject.STATUS_ADOPTAT_FINAL:
            stale_projects.append({"project": p, "days": days, "since": since_dt})
    # Sortare: cele mai multe zile în același status, apoi deadline-ul cel mai apropiat
    from datetime import date as _date

    stale_projects.sort(
        key=lambda r: (
            -int(r["days"]),
            (r["project"].termen_deadline or _date.max),
            (r["project"].titlu or ""),
        )
    )
    stale_projects_top = stale_projects[:30]

    # Distribuție stagnare pe status
    stale_by_status = {}
    for r in stale_projects:
        code = r["project"].status_implementare
        stale_by_status[code] = stale_by_status.get(code, 0) + 1

    stale_total = len(stale_projects)
    stale_pct_total = round((stale_total / total) * 100, 1) if total else 0.0
    stale_status_rows = []
    for code, label in PnaProject.STATUS_IMPLEMENTARE_CHOICES:
        nr = int(stale_by_status.get(code, 0))
        pct = round((nr / stale_total) * 100, 1) if stale_total else 0.0
        stale_status_rows.append({"code": code, "label": label, "nr": nr, "pct": pct})

    # Activitate status: ultimele 12 luni (excludem baseline-ul cu from_status="")
    from django.db.models.functions import TruncMonth

    start_month = (timezone.now().date().replace(day=1) - timedelta(days=365)).replace(day=1)
    activity_qs = (
        PnaProjectStatusHistory.objects.filter(project_id__in=project_ids, changed_at__date__gte=start_month, from_status__gt="")
        .annotate(month=TruncMonth("changed_at"))
        .values("month")
        .annotate(nr=Count("id"))
        .order_by("month")
    )
    activity_map = {r["month"].date(): int(r["nr"]) for r in activity_qs if r.get("month")}

    # Construim lista de luni (12) până la luna curentă inclusiv (corect pe luni)
    def _shift_month(d, delta_months: int):
        y = d.year
        m = d.month + delta_months
        while m <= 0:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        return d.replace(year=y, month=m, day=1)

    months_activity = []
    cur = timezone.now().date().replace(day=1)
    for i in range(11, -1, -1):
        m = _shift_month(cur, -i)
        months_activity.append({"month": m, "nr": activity_map.get(m, 0)})
    max_act = max([r["nr"] for r in months_activity] + [0])
    for r in months_activity:
        r["bar_h"] = round((r["nr"] / max_act) * 100, 1) if max_act else 0.0

    status_moves_30 = (
        PnaProjectStatusHistory.objects.filter(project_id__in=project_ids, changed_at__gte=(timezone.now() - timedelta(days=30)), from_status__gt="")
        .count()
    )

    # -------------------- KPI-uri rapide --------------------
    nr_no_deadline = 0
    nr_overdue = 0
    nr_upcoming_30 = 0
    nr_upcoming_60 = 0
    nr_upcoming_90 = 0

    nr_necesita_ce = 0
    nr_expertiza_externa = 0
    nr_expertiza_interna_insuf = 0
    nr_expertiza_externa_fara_furnizor = 0

    nr_missing_cost = 0
    nr_missing_volum = 0
    nr_missing_institutie = 0
    nr_missing_acte = 0

    from decimal import Decimal

    sum_cost = {2026: Decimal("0"), 2027: Decimal("0"), 2028: Decimal("0"), 2029: Decimal("0")}
    sum_zile = 0

    # pentru acte UE
    acte_tip_counts = {}
    transp_counts = {"TOTAL": 0, "PARTIAL": 0, "": 0}
    act_to_projects = {}  # act_id -> set(project_id)

    # derapaje (schimbare termen)
    derapaje = []

    # risc (scor simplu)
    risk_rows = []

    def _months_diff(d1, d2):
        """Diferență aproximativă în luni între două date (bazată pe an/lună)."""
        if not d1 or not d2:
            return 0
        return (d2.year - d1.year) * 12 + (d2.month - d1.month)

    for p in proiecte:
        deadline = p.termen_deadline
        if not deadline:
            nr_no_deadline += 1
        else:
            if deadline < today:
                nr_overdue += 1
            if today <= deadline <= (today + timedelta(days=30)):
                nr_upcoming_30 += 1
            if today <= deadline <= (today + timedelta(days=60)):
                nr_upcoming_60 += 1
            if today <= deadline <= (today + timedelta(days=90)):
                nr_upcoming_90 += 1

        if p.necesita_avizare_comisia_europeana:
            nr_necesita_ce += 1

        if p.necesita_expertiza_externa:
            nr_expertiza_externa += 1
            if not p.este_identificata_expertiza_externa:
                nr_expertiza_externa_fara_furnizor += 1

        if p.expertiza_interna == 1:
            nr_expertiza_interna_insuf += 1

        # costuri
        any_cost = False
        for y in (2026, 2027, 2028, 2029):
            val = getattr(p, f"cost_{y}")
            if val is not None:
                any_cost = True
                try:
                    sum_cost[y] += Decimal(str(val))
                except Exception:
                    pass
        if not any_cost:
            nr_missing_cost += 1

        # volum
        if p.volum_munca_zile is None:
            nr_missing_volum += 1
        else:
            try:
                sum_zile += int(p.volum_munca_zile)
            except Exception:
                pass

        # instituție principală
        if not p.institutie_principala_ref_id and not (p.institutie_principala or "").strip():
            nr_missing_institutie += 1

        # acte UE
        links = list(getattr(p, "acte_ue_legaturi").all())
        if not links:
            nr_missing_acte += 1
        for l in links:
            act = l.eu_act
            if not act:
                continue
            tip = (act.tip_document or "").strip() or "(nespecificat)"
            acte_tip_counts[tip] = acte_tip_counts.get(tip, 0) + 1
            ttr = (l.tip_transpunere or "").strip()
            if ttr in transp_counts:
                transp_counts[ttr] += 1
            else:
                transp_counts[ttr] = transp_counts.get(ttr, 0) + 1
            act_to_projects.setdefault(act.id, set()).add(p.id)

        # derapaj (doar dacă există termen actualizat)
        if p.termen_actualizat_aprobare_guvern:
            baseline = p.termen_aprobare_guvern or p.termen_aprobare_parlament
            if baseline:
                diff_m = _months_diff(baseline, p.termen_actualizat_aprobare_guvern)
                if diff_m != 0:
                    derapaje.append(
                        {
                            "project": p,
                            "baseline": baseline,
                            "updated": p.termen_actualizat_aprobare_guvern,
                            "diff_months": diff_m,
                        }
                    )

        # risc – scor euristic
        score = 0
        flags = []
        if not deadline:
            score += 2
            flags.append("Fără deadline")
        else:
            if deadline < today:
                score += 3
                flags.append("Termen depășit")
            elif deadline <= (today + timedelta(days=60)):
                score += 2
                flags.append("Termen < 60 zile")

        if p.prioritate == 3:
            score += 1
            flags.append("Prioritate înaltă")
        if p.complexitate and p.complexitate >= 4:
            score += 1
            flags.append("Complexitate ridicată")
        if p.expertiza_interna == 1:
            score += 2
            flags.append("Expertiză internă insuficientă")
        elif p.expertiza_interna == 2:
            score += 1
            flags.append("Expertiză internă parțială")
        if p.necesita_expertiza_externa:
            score += 2
            flags.append("Expertiză externă necesară")
            if not p.este_identificata_expertiza_externa:
                score += 2
                flags.append("Fără expertiză externă identificată")
        if p.necesita_avizare_comisia_europeana:
            score += 2
            flags.append("Necesită avizare/coord. CE")
            if p.status_implementare != PnaProject.STATUS_COORDONARE_CE:
                score += 1
                flags.append("Status ≠ coordonare CE")

        if score > 0:
            risk_rows.append({"project": p, "score": score, "flags": flags})

    # -------------------- distribuție pe status --------------------
    counts = {code: 0 for code, _ in PnaProject.STATUS_IMPLEMENTARE_CHOICES}
    for p in proiecte:
        counts[p.status_implementare] = counts.get(p.status_implementare, 0) + 1

    status_rows = []
    max_nr = max(counts.values()) if counts else 0
    for code, label in PnaProject.STATUS_IMPLEMENTARE_CHOICES:
        nr = counts.get(code, 0)
        pct = round((nr / total) * 100, 1) if total else 0.0
        # pentru bare CSS: raport față de maxim (nu % din total) – arată vizibil și când un status e mic
        height_pct = round((nr / max_nr) * 100, 1) if max_nr else 0.0
        status_rows.append(
            {
                "code": code,
                "label": label,
                "nr": nr,
                "pct": pct,
                "bar_h": height_pct,
            }
        )

    # -------------------- matrice pe luni --------------------
    years = sorted({p.termen_deadline.year for p in proiecte if p.termen_deadline})
    try:
        selected_year = int(request.GET.get("year") or 0)
    except Exception:
        selected_year = 0
    if not selected_year:
        selected_year = today.year
    if years and selected_year not in years:
        selected_year = years[-1]

    months = [
        (1, "Ianuarie"),
        (2, "Februarie"),
        (3, "Martie"),
        (4, "Aprilie"),
        (5, "Mai"),
        (6, "Iunie"),
        (7, "Iulie"),
        (8, "August"),
        (9, "Septembrie"),
        (10, "Octombrie"),
        (11, "Noiembrie"),
        (12, "Decembrie"),
    ]

    from collections import defaultdict

    matrix = defaultdict(lambda: defaultdict(int))
    for p in proiecte:
        d = p.termen_deadline
        if not d or d.year != selected_year:
            continue
        if p.criterion_id:
            key = ("CR", p.criterion_id)
        else:
            key = ("CH", p.chapter_id)
        if not key[1]:
            continue
        if mode == "days":
            matrix[key][d.month] += int(p.volum_munca_zile or 0)
        else:
            matrix[key][d.month] += 1

    criterii_rows = []
    criterii = list(Criterion.objects.all().order_by("cod"))
    for cr in criterii:
        vals = [matrix[("CR", cr.id)].get(m, 0) for m, _ in months]
        if sum(vals) == 0:
            continue
        criterii_rows.append({"obj": cr, "counts": vals, "total": sum(vals)})

    chapter_cluster_rows = []
    grouped_chapters = group_chapters_by_cluster()
    for cl, chapters in grouped_chapters:
        rows = []
        for ch in chapters:
            vals = [matrix[("CH", ch.id)].get(m, 0) for m, _ in months]
            if sum(vals) == 0:
                continue
            rows.append({"obj": ch, "counts": vals, "total": sum(vals)})
        if rows:
            chapter_cluster_rows.append({"cluster": cl, "rows": rows})

    # -------------------- liste utile --------------------
    overdue = [p for p in proiecte if (p.termen_deadline and p.termen_deadline < today)]
    upcoming = [p for p in proiecte if (p.termen_deadline and today <= p.termen_deadline <= (today + timedelta(days=90)))]
    overdue.sort(key=lambda p: (p.termen_deadline or datetime.max.date(), p.titlu))
    upcoming.sort(key=lambda p: (p.termen_deadline or datetime.max.date(), p.titlu))

    # top derapaje
    derapaje.sort(key=lambda r: abs(r["diff_months"]), reverse=True)
    derapaje_top = derapaje[:20]

    # top riscuri
    risk_rows.sort(key=lambda r: (r["score"], (r["project"].termen_deadline or datetime.max.date())), reverse=True)
    top_risks = risk_rows[:20]

    def _stage_bucket(status_code: str) -> str:
        if status_code == PnaProject.STATUS_NEINITIAT:
            return "neinitiate"
        if status_code in {
            PnaProject.STATUS_INITIAT_GUVERN,
            PnaProject.STATUS_AVIZARE_GUVERN,
            PnaProject.STATUS_COORDONARE_CE,
            PnaProject.STATUS_APROBARE_GUVERN,
        }:
            return "guvern"
        if status_code in {
            PnaProject.STATUS_INITIAT_PARLAMENT,
            PnaProject.STATUS_AVIZARE_PARLAMENT,
        }:
            return "parlament"
        if status_code == PnaProject.STATUS_ADOPTAT_FINAL:
            return "adoptat"
        return "other"

    def _empty_resource_row(label, filter_url=None, dashboard_url=None):
        return {
            "label": label,
            "filter_url": filter_url,
            "dashboard_url": dashboard_url,
            "total": 0,
            "neinitiate": 0,
            "guvern": 0,
            "parlament": 0,
            "adoptat": 0,
            "overdue": 0,
            "upcoming60": 0,
            "zile": 0,
            "cost_total": Decimal("0"),
        }

    def _update_resource_row(row, p):
        row["total"] += 1
        row[_stage_bucket(p.status_implementare)] = row.get(_stage_bucket(p.status_implementare), 0) + 1
        dl = p.termen_deadline
        if dl and dl < today:
            row["overdue"] += 1
        if dl and today <= dl <= (today + timedelta(days=60)):
            row["upcoming60"] += 1
        if p.volum_munca_zile:
            try:
                row["zile"] += int(p.volum_munca_zile)
            except Exception:
                pass
        for y in (2026, 2027, 2028, 2029):
            val = getattr(p, f"cost_{y}")
            if val is not None:
                try:
                    row["cost_total"] += Decimal(str(val))
                except Exception:
                    pass

    # top instituții
    inst_agg = {}
    for p in proiecte:
        inst = p.institutie_principala_ref
        key = inst.id if inst else 0
        name = inst.nume if inst else "(nespecificat)"
        row = inst_agg.setdefault(
            key,
            {
                "id": inst.id if inst else None,
                "name": name,
                **_empty_resource_row(
                    name,
                    filter_url=(reverse("admin_pna_filtered_list") + f"?institution={inst.id}{'&' + scope_params if scope_params else ''}") if inst else _filtered_list_url({"missing_institution": 1}),
                    dashboard_url=reverse("admin_pna_dashboard_institution", kwargs={"pk": inst.id}) if inst else None,
                ),
                "id": inst.id if inst else None,
                "name": name,
            },
        )
        _update_resource_row(row, p)

    inst_rows = list(inst_agg.values())
    inst_rows.sort(key=lambda r: (r["total"], r["zile"]), reverse=True)
    resource_institution_rows = inst_rows[:20]

    # Pentru dashboard-ul per instituție, un breakdown mai util este pe capitole/foi de parcurs.
    top_scopes = None
    if scope_kind == "institution":
        scope_agg = {}
        for p in proiecte:
            if p.criterion_id and p.criterion:
                kind = "CR"
                sid = p.criterion_id
                label = f"{p.criterion.cod} — {p.criterion.denumire}"
                dashboard_url = reverse("admin_pna_dashboard_criterion", kwargs={"pk": p.criterion_id})
            elif p.chapter_id and p.chapter:
                kind = "CH"
                sid = p.chapter_id
                label = f"Cap. {p.chapter.numar} — {p.chapter.denumire}"
                dashboard_url = reverse("admin_pna_dashboard_chapter", kwargs={"pk": p.chapter_id})
            else:
                continue

            key = (kind, sid)
            row = scope_agg.setdefault(
                key,
                {
                    "kind": kind,
                    "id": sid,
                    "label": label,
                    "dashboard_url": dashboard_url,
                    "filter_url": _filtered_list_url({"chapter": sid}) if kind == "CH" else _filtered_list_url({"criterion": sid}),
                    **_empty_resource_row(
                        label,
                        filter_url=_filtered_list_url({"chapter": sid}) if kind == "CH" else _filtered_list_url({"criterion": sid}),
                        dashboard_url=dashboard_url,
                    ),
                    "kind": kind,
                    "id": sid,
                    "label": label,
                },
            )
            _update_resource_row(row, p)

        rows = list(scope_agg.values())
        rows.sort(key=lambda r: (r["total"], r["zile"]), reverse=True)
        top_scopes = rows[:20]

    # Resurse & expertiză pe capitole și foi de parcurs
    resource_chapter_groups = []
    for cl, chapters in grouped_chapters:
        rows = []
        for ch in chapters:
            projects_for_ch = [p for p in proiecte if p.chapter_id == ch.id]
            if not projects_for_ch:
                continue
            row = _empty_resource_row(
                f"Cap. {ch.numar} — {ch.denumire}",
                filter_url=_filtered_list_url({"chapter": ch.id}),
                dashboard_url=reverse("admin_pna_dashboard_chapter", kwargs={"pk": ch.id}),
            )
            row["obj"] = ch
            for p in projects_for_ch:
                _update_resource_row(row, p)
            rows.append(row)
        if rows:
            resource_chapter_groups.append({"cluster": cl, "rows": rows})

    resource_criteria_rows = []
    for cr in Criterion.objects.all().order_by("cod"):
        projects_for_cr = [p for p in proiecte if p.criterion_id == cr.id]
        if not projects_for_cr:
            continue
        row = _empty_resource_row(
            f"{cr.cod} — {cr.denumire}",
            filter_url=_filtered_list_url({"criterion": cr.id}),
            dashboard_url=reverse("admin_pna_dashboard_criterion", kwargs={"pk": cr.id}),
        )
        row["obj"] = cr
        for p in projects_for_cr:
            _update_resource_row(row, p)
        resource_criteria_rows.append(row)

    # Top acte UE (după nr proiecte)
    top_acte = []
    for act_id, proj_set in act_to_projects.items():
        top_acte.append((act_id, len(proj_set)))
    top_acte.sort(key=lambda x: x[1], reverse=True)
    top_acte = top_acte[:15]
    act_lookup = {l.eu_act.id: l.eu_act for p in proiecte for l in getattr(p, "acte_ue_legaturi").all() if l.eu_act}
    top_acte_rows = []
    for act_id, cnt in top_acte:
        act = act_lookup.get(act_id)
        if not act:
            continue
        top_acte_rows.append({"act": act, "nr_projects": cnt})

    # Distribuție tip acte UE (Top 10)
    acte_tip_rows = [{"tip": k, "nr": v} for k, v in acte_tip_counts.items()]
    acte_tip_rows.sort(key=lambda r: r["nr"], reverse=True)
    acte_tip_rows = acte_tip_rows[:12]

    # Distribuție transpunere
    transp_rows = []
    for code, label in PnaProjectEUAct.TIP_TRANSPUNERE_CHOICES:
        transp_rows.append({"code": code, "label": label, "nr": transp_counts.get(code, 0)})

    # Acțiuni recomandate / calitate date (cu linkuri)
    actions = [
        {
            "label": f"Stagnante ≥ {stale_days_threshold} zile (fără schimbare status)",
            "nr": stale_total,
            "href": _filtered_list_url({"stale_days": stale_days_threshold}),
        },
        {
            "label": "Schimbări status (ultimele 30 zile)",
            "nr": status_moves_30,
            "href": _filtered_list_url({"status_changed_days": 30}),
        },
        {
            "label": "Deadline-uri depășite",
            "nr": nr_overdue,
            "href": _filtered_list_url({"overdue": 1}),
        },
        {
            "label": "Deadline-uri în următoarele 60 zile",
            "nr": nr_upcoming_60,
            "href": _filtered_list_url({"upcoming_days": 60}),
        },
        {
            "label": "Fără deadline",
            "nr": nr_no_deadline,
            "href": _filtered_list_url({"missing_deadline": 1}),
        },
        {
            "label": "Necesită coordonare/avizare CE",
            "nr": nr_necesita_ce,
            "href": _filtered_list_url({"needs_ce": 1}),
        },
        {
            "label": "Necesită expertiză externă",
            "nr": nr_expertiza_externa,
            "href": _filtered_list_url({"needs_external": 1}),
        },
        {
            "label": "Expertiză externă necesară (neidentificată)",
            "nr": nr_expertiza_externa_fara_furnizor,
            "href": _filtered_list_url({"external_provider_missing": 1}),
        },
        {
            "label": "Expertiză internă insuficientă",
            "nr": nr_expertiza_interna_insuf,
            "href": _filtered_list_url({"internal_expertise": 1}),
        },
        {
            "label": "Fără instituție principală",
            "nr": nr_missing_institutie,
            "href": _filtered_list_url({"missing_institution": 1}),
        },
        {
            "label": "Fără acte UE atașate",
            "nr": nr_missing_acte,
            "href": _filtered_list_url({"missing_acts": 1}),
        },
        {
            "label": "Fără costuri (2026–2029)",
            "nr": nr_missing_cost,
            "href": _filtered_list_url({"missing_cost": 1}),
        },
        {
            "label": "Fără estimare volum muncă",
            "nr": nr_missing_volum,
            "href": _filtered_list_url({"missing_volum": 1}),
        },
        {
            "label": "Necesită CE dar status ≠ coordonare CE",
            "nr": len([p for p in proiecte if p.necesita_avizare_comisia_europeana and p.status_implementare != PnaProject.STATUS_COORDONARE_CE]),
            "href": _filtered_list_url({"ce_status_mismatch": 1}),
        },
    ]

    # cost rows
    cost_rows = [
        {"year": 2026, "nr": sum_cost[2026]},
        {"year": 2027, "nr": sum_cost[2027]},
        {"year": 2028, "nr": sum_cost[2028]},
        {"year": 2029, "nr": sum_cost[2029]},
    ]
    max_cost = max([float(r["nr"]) for r in cost_rows] + [0.0])
    for r in cost_rows:
        v = float(r["nr"]) if r["nr"] is not None else 0.0
        r["bar_h"] = round((v / max_cost) * 100, 1) if max_cost else 0.0

    return render(
        request,
        "portal/admin_pna_dashboard.html",
        {
            "can_edit_pna": can_edit_pna(request.user),
            "scope_kind": scope_kind,
            "scope_title": scope_title,
            "scope_params": scope_params,
            "include_co": include_co,
            "toggle_include_co_url": toggle_include_co_url,
            "back_url": back_url,
            "back_label": back_label,
            "filtered_list_url": _filtered_list_url(),
            "total": total,
            "nr_neinitiate": nr_neinitiate,
            "nr_in_procedura_guvern": nr_in_procedura_guvern,
            "nr_in_procedura_parlament": nr_in_procedura_parlament,
            "nr_adoptate_final": nr_adoptate_final,
            "stale_days_threshold": stale_days_threshold,
            "stale_total": stale_total,
            "stale_pct_total": stale_pct_total,
            "stale_projects_top": stale_projects_top,
            "stale_status_rows": stale_status_rows,
            "status_moves_30": status_moves_30,
            "months_activity": months_activity,
            "mode": mode,
            "status_rows": status_rows,
            "years": years or [selected_year],
            "selected_year": selected_year,
            "months": months,
            "criterii_rows": criterii_rows,
            "chapter_cluster_rows": chapter_cluster_rows,
            "nr_overdue": nr_overdue,
            "nr_upcoming_90": nr_upcoming_90,
            "nr_upcoming_30": nr_upcoming_30,
            "nr_upcoming_60": nr_upcoming_60,
            "nr_no_deadline": nr_no_deadline,
            "nr_necesita_ce": nr_necesita_ce,
            "nr_expertiza_externa": nr_expertiza_externa,
            "nr_expertiza_interna_insuf": nr_expertiza_interna_insuf,
            "nr_ext_fara_furnizor": nr_expertiza_externa_fara_furnizor,
            "nr_missing_cost": nr_missing_cost,
            "nr_missing_volum": nr_missing_volum,
            "nr_missing_institutie": nr_missing_institutie,
            "nr_missing_acte": nr_missing_acte,
            "sum_zile": sum_zile,
            "cost_rows": cost_rows,
            "overdue": overdue[:50],
            "upcoming": upcoming[:50],
            "top_risks": top_risks,
            "derapaje_top": derapaje_top,
            "resource_institution_rows": resource_institution_rows,
            "resource_chapter_groups": resource_chapter_groups,
            "resource_criteria_rows": resource_criteria_rows,
            "top_scopes": top_scopes,
            "acte_tip_rows": acte_tip_rows,
            "transp_rows": transp_rows,
            "top_acte_rows": top_acte_rows,
            "actions": actions,
            "latest_contribution_rows": latest_contribution_rows,
            "all_contributions_url": all_contributions_url,

            # --- contribuții experți (etapa 2) ---
            "contrib_summary_cards": contrib_summary_cards,
            "contrib_dims_rows": contrib_dims_rows,
            "contrib_missing_top": contrib_missing_top,
            "contrib_missing_url": contrib_missing_url,
            "contrib_chapter_groups": contrib_chapter_groups,
            "contrib_criterii_rows": contrib_criterii_rows,
            "contrib_institution_rows": contrib_institution_rows,
        },
    )


@user_passes_test(can_edit_pna)
def admin_pna_create(request):
    ActeFormSet = formset_factory(PnaEUActInlineForm, extra=1, can_delete=True)

    if request.method == "POST":
        form = PnaProjectForm(request.POST)
        acte_formset = ActeFormSet(request.POST, prefix="acts")
        if form.is_valid() and acte_formset.is_valid():
            obj = form.save(commit=False)
            obj.creat_de = request.user
            obj.save()
            form.save_m2m()
            form.sync_institution_legacy_fields(obj)

            # -------------------- istoric (etapa 2) --------------------
            # Status baseline la creare
            PnaProjectStatusHistory.objects.create(
                project=obj,
                from_status="",
                to_status=obj.status_implementare or "",
                changed_by=request.user,
                source=PnaProjectStatusHistory.SOURCE_UI,
                note="Creare proiect",
            )

            # Termene baseline la creare (doar câmpurile completate)
            for field_name in [
                PnaProjectDeadlineHistory.FIELD_GOV,
                PnaProjectDeadlineHistory.FIELD_PARL,
                PnaProjectDeadlineHistory.FIELD_GOV_UPDATED,
                PnaProjectDeadlineHistory.FIELD_PARL_CONSULT,
            ]:
                val = getattr(obj, field_name, None)
                if val is None:
                    continue
                PnaProjectDeadlineHistory.objects.create(
                    project=obj,
                    field=field_name,
                    old_value=None,
                    new_value=val,
                    changed_by=request.user,
                    source=PnaProjectDeadlineHistory.SOURCE_UI,
                    note="Creare proiect",
                )

            # Salvare acte UE din formset
            for cd in acte_formset.cleaned_data:
                if not cd or cd.get("DELETE") or cd.get("_empty"):
                    continue
                celex, url = _extract_celex_from_link_or_code(cd.get("link_celex") or "")
                if not celex:
                    continue
                den = (cd.get("denumire") or "").strip()
                tip_doc = (cd.get("tip_document") or "").strip()
                tip_tr = (cd.get("tip_transpunere") or "").strip()
                act, _ = EUAct.objects.get_or_create(
                    celex=celex,
                    defaults={
                        "denumire": den or celex,
                        "tip_document": tip_doc,
                        "url": url,
                    },
                )
                changed = False
                if den and act.denumire != den:
                    act.denumire = den
                    changed = True
                if tip_doc and act.tip_document != tip_doc:
                    act.tip_document = tip_doc
                    changed = True
                if url and act.url != url:
                    act.url = url
                    changed = True
                if changed:
                    act.save()

                link_obj, _created = PnaProjectEUAct.objects.get_or_create(project=obj, eu_act=act)
                if link_obj.tip_transpunere != (tip_tr or ""):
                    link_obj.tip_transpunere = tip_tr or ""
                    link_obj.save(update_fields=["tip_transpunere"])

            messages.success(request, "Proiectul PNA a fost creat.")
            return redirect("admin_pna_detail", pk=obj.pk)
    else:
        form = PnaProjectForm()
        acte_formset = ActeFormSet(prefix="acts")

    return render(
        request,
        "portal/admin_pna_form.html",
        {
            "form": form,
            "acte_formset": acte_formset,
            "titlu_pagina": "Proiect PNA nou",
        },
    )


@user_passes_test(can_edit_pna)
def admin_pna_edit(request, pk: int):
    obj = get_object_or_404(PnaProject, pk=pk)

    ActeFormSet = formset_factory(PnaEUActInlineForm, extra=1, can_delete=True)

    # Legături existente (pentru pre-populare și update/delete)
    existing_links = list(obj.acte_ue_legaturi.select_related("eu_act").all())
    existing_by_id = {l.id: l for l in existing_links}

    if request.method == "POST":
        # snapshot înainte de save pentru istoric
        old_status = obj.status_implementare
        old_deadlines = {
            PnaProjectDeadlineHistory.FIELD_GOV: obj.termen_aprobare_guvern,
            PnaProjectDeadlineHistory.FIELD_PARL: obj.termen_aprobare_parlament,
            PnaProjectDeadlineHistory.FIELD_GOV_UPDATED: obj.termen_actualizat_aprobare_guvern,
            PnaProjectDeadlineHistory.FIELD_PARL_CONSULT: obj.consultari_publice_parlament,
        }

        form = PnaProjectForm(request.POST, instance=obj)
        acte_formset = ActeFormSet(request.POST, prefix="acts")
        if form.is_valid() and acte_formset.is_valid():
            obj = form.save()
            form.sync_institution_legacy_fields(obj)

            # -------------------- istoric (etapa 2) --------------------
            # status
            if old_status != obj.status_implementare:
                PnaProjectStatusHistory.objects.create(
                    project=obj,
                    from_status=old_status or "",
                    to_status=obj.status_implementare or "",
                    changed_by=request.user,
                    source=PnaProjectStatusHistory.SOURCE_UI,
                    note="Editare proiect",
                )

            # termene
            for field_name, old_val in old_deadlines.items():
                new_val = getattr(obj, field_name, None)
                if old_val != new_val:
                    PnaProjectDeadlineHistory.objects.create(
                        project=obj,
                        field=field_name,
                        old_value=old_val,
                        new_value=new_val,
                        changed_by=request.user,
                        source=PnaProjectDeadlineHistory.SOURCE_UI,
                        note="Editare proiect",
                    )

            for cd in acte_formset.cleaned_data:
                if not cd or cd.get("_empty"):
                    continue

                link_id = cd.get("link_id")
                is_delete = bool(cd.get("DELETE"))

                # Ștergere legătură existentă
                if link_id and is_delete:
                    link_obj = existing_by_id.get(int(link_id))
                    if link_obj:
                        link_obj.delete()
                    continue

                # rând nou șters → ignorăm
                if (not link_id) and is_delete:
                    continue

                celex, url = _extract_celex_from_link_or_code(cd.get("link_celex") or "")
                if not celex:
                    continue

                den = (cd.get("denumire") or "").strip()
                tip_doc = (cd.get("tip_document") or "").strip()
                tip_tr = (cd.get("tip_transpunere") or "").strip()

                act, _ = EUAct.objects.get_or_create(
                    celex=celex,
                    defaults={
                        "denumire": den or celex,
                        "tip_document": tip_doc,
                        "url": url,
                    },
                )
                changed = False
                if den and act.denumire != den:
                    act.denumire = den
                    changed = True
                # permitem actualizarea tipului chiar dacă e gol (admin poate să îl golească)
                if act.tip_document != (tip_doc or ""):
                    act.tip_document = tip_doc or ""
                    changed = True
                if url and act.url != url:
                    act.url = url
                    changed = True
                if changed:
                    act.save()

                if link_id:
                    link_obj = existing_by_id.get(int(link_id))
                    if not link_obj:
                        # fallback: dacă între timp a dispărut, tratăm ca rând nou
                        link_obj, _ = PnaProjectEUAct.objects.get_or_create(project=obj, eu_act=act)
                    else:
                        # dacă admin a schimbat CELEX-ul pe un act deja existent în proiect,
                        # evităm încălcarea unique_together prin merge.
                        if link_obj.eu_act_id != act.id:
                            other = PnaProjectEUAct.objects.filter(project=obj, eu_act=act).first()
                            if other:
                                if other.tip_transpunere != (tip_tr or ""):
                                    other.tip_transpunere = tip_tr or ""
                                    other.save(update_fields=["tip_transpunere"])
                                link_obj.delete()
                                continue
                            link_obj.eu_act = act
                        # tip transpunere poate fi setat/șters
                        if link_obj.tip_transpunere != (tip_tr or ""):
                            link_obj.tip_transpunere = tip_tr or ""
                        link_obj.save()
                else:
                    # rând nou
                    link_obj, _ = PnaProjectEUAct.objects.get_or_create(project=obj, eu_act=act)
                    if link_obj.tip_transpunere != (tip_tr or ""):
                        link_obj.tip_transpunere = tip_tr or ""
                        link_obj.save(update_fields=["tip_transpunere"])

            messages.success(request, "Proiectul PNA a fost actualizat.")
            return redirect("admin_pna_detail", pk=obj.pk)
    else:
        form = PnaProjectForm(instance=obj)

        initial = [
            {
                "link_id": link.id,
                "link_celex": link.eu_act.celex,
                "denumire": link.eu_act.denumire,
                "tip_document": link.eu_act.tip_document,
                "tip_transpunere": link.tip_transpunere,
            }
            for link in existing_links
        ]
        acte_formset = ActeFormSet(prefix="acts", initial=initial)

    return render(
        request,
        "portal/admin_pna_form.html",
        {
            "form": form,
            "acte_formset": acte_formset,
            "titlu_pagina": "Editare proiect PNA",
            "obj": obj,
        },
    )


@user_passes_test(is_internal)
def admin_pna_detail(request, pk: int):
    """Fișa proiectului PNA (doar vizualizare).

    Adăugarea / scoaterea actelor UE se face doar din pagina de editare.
    """

    obj = get_object_or_404(
        PnaProject.objects.select_related("chapter", "criterion", "institutie_principala_ref")
        .prefetch_related("acte_ue_legaturi__eu_act")
        .prefetch_related("institutii_responsabile"),
        pk=pk,
    )

    acts = list(obj.acte_ue_legaturi.select_related("eu_act").all())

    # Istoric (status + termene) – doar pentru admin
    can_view_history = bool(request.user.is_superuser)
    status_hist = list(
        obj.status_history.select_related("changed_by").all()[:100]
    ) if can_view_history else []
    deadline_hist = list(
        obj.deadline_history.select_related("changed_by").all()[:100]
    ) if can_view_history else []

    contributii_rows = []
    contributii_qs = (
        PnaExpertContribution.objects.filter(project=obj)
        .select_related("expert", "expert__profil_expert")
        .order_by("expert__last_name", "expert__first_name", "expert__username")
    )
    for c in contributii_qs:
        if not c.are_orice:
            continue
        profil = getattr(c.expert, "profil_expert", None)
        contributii_rows.append({
            "expert": c.expert,
            "profil": profil,
            "contrib": c,
            "has_flex": bool((c.flexibilitate or "").strip()),
            "has_comp": bool((c.compensare or "").strip()),
            "has_tran": bool((c.tranzitie or "").strip()),
        })

    return render(
        request,
        "portal/admin_pna_detail.html",
        {
            "obj": obj,
            "acts": acts,
            "status_hist": status_hist,
            "deadline_hist": deadline_hist,
            "contributii_rows": contributii_rows,
            "can_edit_pna": can_edit_pna(request.user),
            "can_view_history": can_view_history,
        },
    )




@user_passes_test(is_internal)
def admin_pna_all_contributions(request):
    """Listă cu toate contribuțiile experților la proiecte PNA, filtrabilă după scope-ul dashboardului."""
    qs = PnaProject.objects.filter(arhivat=False)

    institution = (request.GET.get("institution") or "").strip()
    chapter_id = (request.GET.get("chapter") or "").strip()
    criterion_id = (request.GET.get("criterion") or "").strip()
    include_co = (request.GET.get("include_co") or "").strip()

    scope_title = None
    back_url = reverse("admin_pna_dashboard")
    back_label = "Înapoi la dashboard"

    if institution:
        try:
            inst = get_object_or_404(PnaInstitution, pk=int(institution))
            if include_co == "1":
                qs = qs.filter(Q(institutie_principala_ref=inst) | Q(institutii_responsabile=inst)).distinct()
            else:
                qs = qs.filter(institutie_principala_ref=inst)
            scope_title = inst.nume
            back_url = reverse("admin_pna_dashboard_institution", kwargs={"pk": inst.pk}) + ("?include_co=1" if include_co == "1" else "")
            back_label = "Înapoi la dashboard instituție"
        except Exception:
            pass
    elif chapter_id:
        try:
            ch = get_object_or_404(Chapter, pk=int(chapter_id))
            qs = qs.filter(chapter=ch)
            scope_title = f"Cap. {ch.numar} — {ch.denumire}"
            back_url = reverse("admin_pna_dashboard_chapter", kwargs={"pk": ch.pk})
            back_label = "Înapoi la dashboard capitol"
        except Exception:
            pass
    elif criterion_id:
        try:
            cr = get_object_or_404(Criterion, pk=int(criterion_id))
            qs = qs.filter(criterion=cr)
            scope_title = f"{cr.cod} — {cr.denumire}"
            back_url = reverse("admin_pna_dashboard_criterion", kwargs={"pk": cr.pk})
            back_label = "Înapoi la dashboard foaie de parcurs"
        except Exception:
            pass

    project_ids = list(qs.values_list("id", flat=True))
    contribs = (
        PnaExpertContribution.objects.filter(project_id__in=project_ids)
        .select_related("project", "expert", "expert__profil_expert")
        .order_by("-updated_at")
    )
    rows = []
    for c in contribs:
        if not c.are_orice:
            continue
        rows.append({
            "project": c.project,
            "expert": c.expert,
            "profil": getattr(c.expert, "profil_expert", None),
            "contrib": c,
            "has_flex": bool((c.flexibilitate or "").strip()),
            "has_comp": bool((c.compensare or "").strip()),
            "has_tran": bool((c.tranzitie or "").strip()),
        })

    return render(request, "portal/admin_pna_all_contributions.html", {
        "rows": rows,
        "scope_title": scope_title,
        "back_url": back_url,
        "back_label": back_label,
    })


@user_passes_test(is_internal)
def admin_pna_contributii(request, pk: int):
    """Contribuțiile experților (Flexibilitate/Compensare/Tranziție) pentru un proiect PNA."""

    proiect = get_object_or_404(
        PnaProject.objects.select_related("chapter", "criterion", "institutie_principala_ref")
        .prefetch_related("acte_ue_legaturi__eu_act"),
        pk=pk,
    )

    # experți "relevanți" pentru proiect (după alocări)
    exp_profiles = ExpertProfile.objects.select_related("user").filter(
        arhivat=False,
        user__is_active=True,
        user__is_staff=False,
    )

    scope_label = ""
    if proiect.chapter_id:
        exp_profiles = exp_profiles.filter(capitole=proiect.chapter_id)
        if proiect.chapter:
            scope_label = f"Cap. {proiect.chapter.numar} — {proiect.chapter.denumire}"
    elif proiect.criterion_id:
        exp_profiles = exp_profiles.filter(criterii=proiect.criterion_id)
        if proiect.criterion:
            scope_label = f"{proiect.criterion.cod} — {proiect.criterion.denumire}"

    experts = [p.user for p in exp_profiles.order_by("user__last_name", "user__first_name")]
    expert_ids = [u.id for u in experts]

    contrib_qs = (
        PnaExpertContribution.objects.filter(project=proiect, expert_id__in=expert_ids)
        .select_related("expert")
        .order_by("expert__last_name", "expert__first_name")
    )
    contrib_by_expert = {c.expert_id: c for c in contrib_qs}

    rows = []
    nr_any = 0
    nr_flex = 0
    nr_comp = 0
    nr_tran = 0

    for u in experts:
        c = contrib_by_expert.get(u.id)
        flex = (c.flexibilitate if c else "") or ""
        comp = (c.compensare if c else "") or ""
        tran = (c.tranzitie if c else "") or ""
        has_any = bool(flex.strip() or comp.strip() or tran.strip())
        if has_any:
            nr_any += 1
        if flex.strip():
            nr_flex += 1
        if comp.strip():
            nr_comp += 1
        if tran.strip():
            nr_tran += 1

        rows.append(
            {
                "expert": u,
                "profil": getattr(u, "profil_expert", None),
                "contrib": c,
                "has_any": has_any,
                "has_flex": bool(flex.strip()),
                "has_comp": bool(comp.strip()),
                "has_tran": bool(tran.strip()),
            }
        )

    # filtru opțional: doar cei cu contribuții
    only = (request.GET.get("only") or "").strip()
    if only == "filled":
        rows = [r for r in rows if r["has_any"]]

    return render(
        request,
        "portal/admin_pna_contributii.html",
        {
            "obj": proiect,
            "scope_label": scope_label,
            "rows": rows,
            "total_experti": len(experts),
            "nr_any": nr_any,
            "nr_flex": nr_flex,
            "nr_comp": nr_comp,
            "nr_tran": nr_tran,
            "only": only,
        },
    )


@user_passes_test(is_internal)
def admin_pna_contributii_expert(request, pk: int, expert_id: int):
    """Detaliu contribuție expert pentru un proiect PNA (read-only)."""

    proiect = get_object_or_404(PnaProject, pk=pk)
    expert = get_object_or_404(User, pk=expert_id, is_staff=False)

    # Siguranță: proiectul trebuie să fie în scope-ul expertului.
    profil = getattr(expert, "profil_expert", None)
    ok = False
    if proiect.chapter_id and profil and profil.capitole.filter(id=proiect.chapter_id).exists():
        ok = True
    if proiect.criterion_id and profil and profil.criterii.filter(id=proiect.criterion_id).exists():
        ok = True
    if not ok:
        raise Http404("Expertul nu este alocat pe acest capitol/foaie de parcurs.")

    contrib = PnaExpertContribution.objects.filter(project=proiect, expert=expert).first()

    return render(
        request,
        "portal/admin_pna_contributii_expert.html",
        {
            "obj": proiect,
            "expert": expert,
            "profil": getattr(expert, "profil_expert", None),
            "contrib": contrib,
        },
    )


@user_passes_test(can_edit_pna)
def admin_pna_detach_act(request, pk: int):
    link = get_object_or_404(PnaProjectEUAct, pk=pk)
    project_id = link.project_id
    if request.method == "POST":
        link.delete()
        messages.success(request, "Actul UE a fost scos din proiect.")
    return redirect("admin_pna_detail", pk=project_id)


@user_passes_test(can_edit_pna)
def admin_pna_arhivare(request, pk: int):
    obj = get_object_or_404(PnaProject, pk=pk)
    if request.method == "POST":
        obj.arhivat = True
        obj.arhivat_la = timezone.now()
        obj.save(update_fields=["arhivat", "arhivat_la"])
        messages.success(request, "Proiectul PNA a fost arhivat.")
        return redirect("admin_pna_list")
    return redirect("admin_pna_detail", pk=obj.pk)


@user_passes_test(can_edit_pna)
def admin_pna_restabilire(request, pk: int):
    obj = get_object_or_404(PnaProject, pk=pk)
    if request.method == "POST":
        obj.arhivat = False
        obj.arhivat_la = None
        obj.save(update_fields=["arhivat", "arhivat_la"])
        messages.success(request, "Proiectul PNA a fost restabilit.")
        return redirect("admin_pna_detail", pk=obj.pk)
    return redirect("admin_pna_detail", pk=obj.pk)


@user_passes_test(is_internal)
def admin_pna_institution_list(request):
    q = (request.GET.get("q") or "").strip()
    qs = PnaInstitution.objects.all().order_by("nume")
    if q:
        qs = qs.filter(nume__icontains=q)

    return render(
        request,
        "portal/admin_pna_institutions_list.html",
        {
            "q": q,
            "institutii": list(qs),
            "can_edit_pna": can_edit_pna(request.user),
        },
    )


@user_passes_test(can_edit_pna)
def admin_pna_institution_create(request):
    if request.method == "POST":
        form = PnaInstitutionForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Instituția a fost adăugată.")
            return redirect("admin_pna_institution_list")
    else:
        form = PnaInstitutionForm()

    return render(
        request,
        "portal/admin_pna_institution_form.html",
        {
            "form": form,
            "titlu_pagina": "Instituție PNA nouă",
        },
    )


@user_passes_test(can_edit_pna)
def admin_pna_institution_edit(request, pk: int):
    obj = get_object_or_404(PnaInstitution, pk=pk)
    if request.method == "POST":
        form = PnaInstitutionForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Instituția a fost actualizată.")
            return redirect("admin_pna_institution_list")
    else:
        form = PnaInstitutionForm(instance=obj)

    return render(
        request,
        "portal/admin_pna_institution_form.html",
        {
            "form": form,
            "titlu_pagina": "Editare instituție PNA",
            "obj": obj,
        },
    )


@user_passes_test(is_internal)
def admin_pna_scope_list(request):
    """Listă proiecte filtrată (folosită din dashboard – click pe matrice)."""

    chapter_id = request.GET.get("chapter")
    criterion_id = request.GET.get("criterion")
    institution_id = request.GET.get("institution")
    year = request.GET.get("year")
    month = request.GET.get("month")

    if not chapter_id and not criterion_id:
        raise Http404("Lipsește filtrul (chapter/criterion).")

    qs = (
        PnaProject.objects.filter(arhivat=False)
        .select_related("chapter", "criterion", "institutie_principala_ref")
        .prefetch_related("institutii_responsabile")
    )

    inst_obj = None
    include_co = (request.GET.get("include_co") or "").strip() == "1"
    if institution_id:
        try:
            inst_obj = get_object_or_404(PnaInstitution, pk=int(institution_id))
            if include_co:
                qs = qs.filter(Q(institutie_principala_ref=inst_obj) | Q(institutii_responsabile=inst_obj)).distinct()
            else:
                qs = qs.filter(institutie_principala_ref=inst_obj)
        except Exception:
            inst_obj = None

    scope_label = ""
    if chapter_id:
        ch = get_object_or_404(Chapter, pk=int(chapter_id))
        qs = qs.filter(chapter=ch)
        scope_label = f"Cap. {ch.numar} — {ch.denumire}"
    else:
        cr = get_object_or_404(Criterion, pk=int(criterion_id))
        qs = qs.filter(criterion=cr)
        scope_label = f"{cr.cod} — {cr.denumire}"

    if inst_obj:
        if include_co:
            scope_label = f"{inst_obj.nume} (principal + co-responsabilă) · {scope_label}"
        else:
            scope_label = f"{inst_obj.nume} · {scope_label}"

    deadline_expr = Coalesce(
        "termen_actualizat_aprobare_guvern",
        "termen_aprobare_parlament",
        "termen_aprobare_guvern",
    )
    qs = qs.annotate(deadline=deadline_expr)

    year_i = None
    month_i = None
    try:
        year_i = int(year) if year else None
    except Exception:
        year_i = None
    try:
        month_i = int(month) if month else None
    except Exception:
        month_i = None

    if year_i:
        qs = qs.filter(deadline__year=year_i)
    if month_i:
        qs = qs.filter(deadline__month=month_i)

    projects = list(qs.order_by("deadline", "titlu"))

    back_dashboard_url = reverse("admin_pna_dashboard")
    back_dashboard_label = "Înapoi la dashboard"
    if inst_obj:
        back_dashboard_url = reverse("admin_pna_dashboard_institution", kwargs={"pk": inst_obj.pk})
        if include_co:
            back_dashboard_url += "?include_co=1"
        back_dashboard_label = "Înapoi la dashboard instituție"
    elif chapter_id:
        try:
            back_dashboard_url = reverse("admin_pna_dashboard_chapter", kwargs={"pk": int(chapter_id)})
            back_dashboard_label = "Înapoi la dashboard capitol"
        except Exception:
            pass
    elif criterion_id:
        try:
            back_dashboard_url = reverse("admin_pna_dashboard_criterion", kwargs={"pk": int(criterion_id)})
            back_dashboard_label = "Înapoi la dashboard foaie de parcurs"
        except Exception:
            pass

    return render(
        request,
        "portal/admin_pna_scope_list.html",
        {
            "scope_label": scope_label,
            "projects": projects,
            "year": year_i,
            "month": month_i,
            "inst_obj": inst_obj,
            "back_dashboard_url": back_dashboard_url,
            "back_dashboard_label": back_dashboard_label,
        },
    )

@user_passes_test(is_internal)
def admin_pna_filtered_list(request):
    """Listă proiecte PNA cu filtre (folosită ca drill-down din dashboard)."""

    qs = (
        PnaProject.objects.filter(arhivat=False)
        .select_related("chapter", "criterion", "institutie_principala_ref")
        .prefetch_related("institutii_responsabile")
    )

    deadline_expr = Coalesce(
        "termen_actualizat_aprobare_guvern",
        "termen_aprobare_parlament",
        "termen_aprobare_guvern",
    )
    qs = qs.annotate(deadline=deadline_expr)

    today = timezone.localdate()

    # -------------------- parametri --------------------
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    stage = (request.GET.get("stage") or "").strip()
    institution = (request.GET.get("institution") or "").strip()
    include_co = (request.GET.get("include_co") or "").strip()

    needs_ce = (request.GET.get("needs_ce") or "").strip()
    needs_external = (request.GET.get("needs_external") or "").strip()
    internal_expertise = (request.GET.get("internal_expertise") or "").strip()

    overdue = (request.GET.get("overdue") or "").strip()
    upcoming_days = (request.GET.get("upcoming_days") or "").strip()

    missing_deadline = (request.GET.get("missing_deadline") or "").strip()
    missing_cost = (request.GET.get("missing_cost") or "").strip()
    missing_volum = (request.GET.get("missing_volum") or "").strip()
    missing_institution = (request.GET.get("missing_institution") or "").strip()
    missing_acts = (request.GET.get("missing_acts") or "").strip()

    # Contribuții experți (PNA etapa 2)
    has_contrib = (request.GET.get("has_contrib") or "").strip()
    missing_contrib = (request.GET.get("missing_contrib") or "").strip()
    missing_flex = (request.GET.get("missing_flex") or "").strip()
    missing_comp = (request.GET.get("missing_comp") or "").strip()
    missing_tran = (request.GET.get("missing_tran") or "").strip()
    missing_all_dims = (request.GET.get("missing_all_dims") or "").strip()

    stale_days = (request.GET.get("stale_days") or "").strip()
    status_changed_days = (request.GET.get("status_changed_days") or "").strip()

    external_provider_missing = (request.GET.get("external_provider_missing") or "").strip()
    ce_status_mismatch = (request.GET.get("ce_status_mismatch") or "").strip()

    chapter_id = (request.GET.get("chapter") or "").strip()
    criterion_id = (request.GET.get("criterion") or "").strip()

    year = (request.GET.get("year") or "").strip()
    month = (request.GET.get("month") or "").strip()

    # -------------------- aplicare filtre --------------------
    if q:
        qs = qs.filter(
            Q(titlu__icontains=q)
            | Q(descriere__icontains=q)
            | Q(institutie_principala_ref__nume__icontains=q)
            | Q(institutie_principala__icontains=q)
        )

    if status:
        qs = qs.filter(status_implementare=status)
    elif stage:
        if stage == "neinitiate":
            qs = qs.filter(status_implementare=PnaProject.STATUS_NEINITIAT)
        elif stage == "guvern":
            qs = qs.filter(status_implementare__in=[
                PnaProject.STATUS_INITIAT_GUVERN,
                PnaProject.STATUS_AVIZARE_GUVERN,
                PnaProject.STATUS_COORDONARE_CE,
                PnaProject.STATUS_APROBARE_GUVERN,
            ])
        elif stage == "parlament":
            qs = qs.filter(status_implementare__in=[
                PnaProject.STATUS_INITIAT_PARLAMENT,
                PnaProject.STATUS_AVIZARE_PARLAMENT,
            ])
        elif stage == "adoptat_final":
            qs = qs.filter(status_implementare=PnaProject.STATUS_ADOPTAT_FINAL)

    inst_obj = None
    if institution:
        try:
            inst_id = int(institution)
            inst_obj = get_object_or_404(PnaInstitution, pk=inst_id)
            if include_co == "1":
                qs = qs.filter(Q(institutie_principala_ref=inst_obj) | Q(institutii_responsabile=inst_obj)).distinct()
            else:
                qs = qs.filter(institutie_principala_ref=inst_obj)
        except Exception:
            inst_obj = None

    if needs_ce == "1":
        qs = qs.filter(necesita_avizare_comisia_europeana=True)
    if needs_external == "1":
        qs = qs.filter(necesita_expertiza_externa=True)

    if internal_expertise:
        try:
            ie = int(internal_expertise)
            qs = qs.filter(expertiza_interna=ie)
        except Exception:
            pass

    if chapter_id:
        try:
            qs = qs.filter(chapter_id=int(chapter_id))
        except Exception:
            pass
    if criterion_id:
        try:
            qs = qs.filter(criterion_id=int(criterion_id))
        except Exception:
            pass

    if year:
        try:
            qs = qs.filter(deadline__year=int(year))
        except Exception:
            pass
    if month:
        try:
            qs = qs.filter(deadline__month=int(month))
        except Exception:
            pass

    if overdue == "1":
        qs = qs.filter(deadline__lt=today)

    if upcoming_days:
        try:
            days = int(upcoming_days)
            qs = qs.filter(deadline__gte=today, deadline__lte=(today + timedelta(days=days)))
        except Exception:
            pass

    if missing_deadline == "1":
        qs = qs.filter(deadline__isnull=True)

    if missing_volum == "1":
        qs = qs.filter(volum_munca_zile__isnull=True)

    if missing_institution == "1":
        qs = qs.filter(institutie_principala_ref__isnull=True).filter(Q(institutie_principala__isnull=True) | Q(institutie_principala=""))

    if missing_cost == "1":
        qs = qs.filter(cost_2026__isnull=True, cost_2027__isnull=True, cost_2028__isnull=True, cost_2029__isnull=True)

    if external_provider_missing == "1":
        qs = qs.filter(necesita_expertiza_externa=True, este_identificata_expertiza_externa=False)

    if ce_status_mismatch == "1":
        qs = qs.filter(necesita_avizare_comisia_europeana=True).exclude(status_implementare=PnaProject.STATUS_COORDONARE_CE)

    # missing acts requires annotate
    if missing_acts == "1":
        qs = qs.annotate(acte_cnt=Count("acte_ue_legaturi", distinct=True)).filter(acte_cnt=0)

    # -------------------- contribuții experți --------------------
    # Numărăm doar contribuțiile care au text în cel puțin una din cele 3 boxe.
    q_f = ~Q(contributii_experti__flexibilitate="")
    q_c = ~Q(contributii_experti__compensare="")
    q_t = ~Q(contributii_experti__tranzitie="")
    q_any = q_f | q_c | q_t

    if has_contrib == "1" or missing_contrib == "1":
        qs = qs.annotate(contrib_any=Count("contributii_experti", filter=q_any, distinct=True))
        if has_contrib == "1":
            qs = qs.filter(contrib_any__gt=0)
        if missing_contrib == "1":
            qs = qs.filter(contrib_any=0)

    if missing_flex == "1":
        qs = qs.annotate(contrib_f=Count("contributii_experti", filter=q_f, distinct=True)).filter(contrib_f=0)

    if missing_comp == "1":
        qs = qs.annotate(contrib_c=Count("contributii_experti", filter=q_c, distinct=True)).filter(contrib_c=0)

    if missing_tran == "1":
        qs = qs.annotate(contrib_t=Count("contributii_experti", filter=q_t, distinct=True)).filter(contrib_t=0)

    if missing_all_dims == "1":
        qs = qs.annotate(
            contrib_f2=Count("contributii_experti", filter=q_f, distinct=True),
            contrib_c2=Count("contributii_experti", filter=q_c, distinct=True),
            contrib_t2=Count("contributii_experti", filter=q_t, distinct=True),
        ).filter(Q(contrib_f2=0) | Q(contrib_c2=0) | Q(contrib_t2=0))

    # Stagnare: proiecte în același status de >= X zile (folosim ultima intrare din status_history)
    if stale_days:
        try:
            days = int(stale_days)
            cutoff = timezone.now() - timedelta(days=days)
            qs = qs.annotate(last_status_change=Max("status_history__changed_at")).filter(last_status_change__lt=cutoff)
            # de regulă nu ne interesează finalul
            qs = qs.exclude(status_implementare=PnaProject.STATUS_ADOPTAT_FINAL)
        except Exception:
            pass

    # Proiecte care au avut cel puțin o schimbare de status în ultimele X zile
    if status_changed_days:
        try:
            days = int(status_changed_days)
            cutoff = timezone.now() - timedelta(days=days)
            qs = qs.filter(status_history__changed_at__gte=cutoff, status_history__from_status__gt="").distinct()
        except Exception:
            pass

    projects = list(qs.order_by("deadline", "titlu"))

    back_dashboard_url = reverse("admin_pna_dashboard")
    back_dashboard_label = "Înapoi la dashboard"
    if inst_obj:
        back_dashboard_url = reverse("admin_pna_dashboard_institution", kwargs={"pk": inst_obj.pk})
        if include_co:
            back_dashboard_url += "?include_co=1"
        back_dashboard_label = "Înapoi la dashboard instituție"
    elif chapter_id:
        try:
            back_dashboard_url = reverse("admin_pna_dashboard_chapter", kwargs={"pk": int(chapter_id)})
            back_dashboard_label = "Înapoi la dashboard capitol"
        except Exception:
            pass
    elif criterion_id:
        try:
            back_dashboard_url = reverse("admin_pna_dashboard_criterion", kwargs={"pk": int(criterion_id)})
            back_dashboard_label = "Înapoi la dashboard foaie de parcurs"
        except Exception:
            pass

    return render(
        request,
        "portal/admin_pna_filtered_list.html",
        {
            "projects": projects,
            "q": q,
            "status": status,
            "status_choices": PnaProject.STATUS_IMPLEMENTARE_CHOICES,
            "institutions": PnaInstitution.objects.all().order_by("nume"),
            "institution": institution,
            "include_co": include_co,
            "inst_obj": inst_obj,
            "back_dashboard_url": back_dashboard_url,
            "back_dashboard_label": back_dashboard_label,
            "needs_ce": needs_ce,
            "needs_external": needs_external,
            "internal_expertise": internal_expertise,
            "overdue": overdue,
            "upcoming_days": upcoming_days,
            "missing_deadline": missing_deadline,
            "missing_cost": missing_cost,
            "missing_volum": missing_volum,
            "missing_institution": missing_institution,
            "missing_acts": missing_acts,

            "has_contrib": has_contrib,
            "missing_contrib": missing_contrib,
            "missing_flex": missing_flex,
            "missing_comp": missing_comp,
            "missing_tran": missing_tran,
            "missing_all_dims": missing_all_dims,
            "stale_days": stale_days,
            "status_changed_days": status_changed_days,
            "external_provider_missing": external_provider_missing,
            "ce_status_mismatch": ce_status_mismatch,
            "year": year,
            "month": month,
            "chapter_id": chapter_id,
            "criterion_id": criterion_id,
            "total": len(projects),
        },
    )



@user_passes_test(can_edit_pna)
def admin_pna_import_template_download(request):
    data = build_pna_import_template_bytes()
    response = HttpResponse(
        data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="template_import_pna.xlsx"'
    return response


@user_passes_test(can_edit_pna)
def admin_pna_import(request):
    """Import masiv PNA.

    Acceptă două formate:
      - fișierul sursă PNA (sheet «Acțiuni_PNA»)
      - template-ul complet de import (sheet-uri «Proiecte_PNA» + «Acte_UE»)
    """

    if request.method == "POST":
        form = PnaImportXLSXForm(request.POST, request.FILES)
        if form.is_valid():
            f = form.cleaned_data["fisier"]
            filename = getattr(f, "name", "") or ""

            try:
                wb = openpyxl.load_workbook(f, data_only=True)
            except Exception:
                messages.error(request, "Fișierul nu poate fi citit. Asigură-te că este .xlsx valid.")
                return redirect("admin_pna_import")

            try:
                result = run_pna_import_workbook(wb, user=request.user)
            except Exception as exc:
                messages.error(request, str(exc))
                return redirect("admin_pna_import")

            rep_buf = io.StringIO()
            rep_w = csv.writer(rep_buf)
            rep_w.writerow(["rand", "email", "status", "mesaj"])
            rep_w.writerows(result["report_rows"])

            run = ImportRun.objects.create(
                kind=ImportRun.KIND_PNA,
                creat_de=request.user,
                nume_fisier=filename,
                nr_create=result["nr_create"],
                nr_actualizate=result["nr_update"],
                nr_erori=result["nr_error"],
                raport_csv=rep_buf.getvalue(),
                cred_csv="",
            )

            mode_label = "template complet" if result.get("mode") == "template" else "fișier PNA"
            messages.success(
                request,
                f"Import PNA finalizat ({mode_label}). Create: {result['nr_create']}, Actualizate: {result['nr_update']}, Erori: {result['nr_error']}.",
            )
            return redirect("admin_import_run_detail", pk=run.pk)
    else:
        form = PnaImportXLSXForm()

    return render(request, "portal/admin_pna_import.html", {"form": form})


@user_passes_test(is_internal)
def admin_general_dashboard(request):
    """Dashboard pentru categoria «General» (chestionare pentru toți experții)."""

    expert_ids_qs = (
        User.objects.filter(is_staff=False, is_active=True)
        .values_list("id", flat=True)
        .distinct()
    )
    expert_ids = list(expert_ids_qs)
    nr_experti = len(expert_ids)

    chestionare_qs = (
        Questionnaire.objects.filter(arhivat=False, este_general=True)
        .distinct()
        .order_by("-creat_la")
    )
    chestionare = list(chestionare_qs)
    nr_chestionare = len(chestionare)

    total_raspunsuri_primite = 0
    rates: list[float] = []

    for q in chestionare:
        nr_experti_q, nr_raspunsuri_q, rata_q, resp_ids = get_questionnaire_rate_and_counts(
            questionnaire=q,
            scope=QuestionnaireScopeSnapshot.SCOPE_GENERAL,
        )

        q.nr_experti_alocati = nr_experti_q
        q.nr_respondenti = nr_raspunsuri_q
        q.proc_respondenti = rata_q
        q.respondenti = list(User.objects.filter(id__in=resp_ids).order_by("last_name", "first_name"))

        total_raspunsuri_primite += int(nr_raspunsuri_q or 0)
        rates.append(float(rata_q or 0.0))

    rata_medie_raspuns = round((sum(rates) / len(rates)), 1) if rates else 0.0

    return render(
        request,
        "portal/admin_general_dashboard.html",
        {
            "nr_experti": nr_experti,
            "nr_chestionare": nr_chestionare,
            "nr_raspunsuri_primite": total_raspunsuri_primite,
            "rata_medie_raspuns": rata_medie_raspuns,
            "chestionare": chestionare,
        },
    )




@user_passes_test(is_internal)
def admin_capitol_dashboard(request, pk: int):
    capitol = get_object_or_404(Chapter, pk=pk)

    expert_ids_qs = (
        User.objects.filter(is_staff=False, is_active=True, profil_expert__capitole=capitol)
        .values_list("id", flat=True)
        .distinct()
    )
    expert_ids = list(expert_ids_qs)
    nr_experti = len(expert_ids)

    chestionare_qs = (
        Questionnaire.objects.filter(arhivat=False, capitole=capitol)
        .distinct()
        .order_by("-creat_la")
    )
    chestionare = list(chestionare_qs)
    nr_chestionare = len(chestionare)

    # Per chestionar:
    # - "Au răspuns" = nr. submisii TRIMIS (un chestionar trimis = 1 răspuns)
    # - "%" = rata de răspuns pentru acel chestionar
    # Pentru chestionarele închise folosim snapshot (înghețat la termen).
    total_raspunsuri_primite = 0
    rates: list[float] = []

    for q in chestionare:
        nr_experti_q, nr_raspunsuri_q, rata_q, resp_ids = get_questionnaire_rate_and_counts(
            questionnaire=q,
            scope=QuestionnaireScopeSnapshot.SCOPE_CHAPTER,
            chapter=capitol,
        )

        # UI
        q.nr_experti_alocati = nr_experti_q
        q.nr_respondenti = nr_raspunsuri_q
        q.proc_respondenti = rata_q
        q.respondenti = list(User.objects.filter(id__in=resp_ids).order_by("last_name", "first_name"))

        total_raspunsuri_primite += int(nr_raspunsuri_q or 0)
        rates.append(float(rata_q or 0.0))

    rata_medie_raspuns = round((sum(rates) / len(rates)), 1) if rates else 0.0

    return render(
        request,
        "portal/admin_capitol_dashboard.html",
        {
            "capitol": capitol,
            "nr_experti": nr_experti,
            "nr_chestionare": nr_chestionare,
            "nr_raspunsuri_primite": total_raspunsuri_primite,
            "rata_medie_raspuns": rata_medie_raspuns,
            "chestionare": chestionare,
        },
    )



@user_passes_test(is_internal)
def admin_criteriu_dashboard(request, pk: int):
    criteriu = get_object_or_404(Criterion, pk=pk)

    expert_ids_qs = (
        User.objects.filter(is_staff=False, is_active=True, profil_expert__criterii=criteriu)
        .values_list("id", flat=True)
        .distinct()
    )
    expert_ids = list(expert_ids_qs)
    nr_experti = len(expert_ids)

    chestionare_qs = (
        Questionnaire.objects.filter(arhivat=False, criterii=criteriu)
        .distinct()
        .order_by("-creat_la")
    )
    chestionare = list(chestionare_qs)
    nr_chestionare = len(chestionare)

    total_raspunsuri_primite = 0
    rates: list[float] = []

    for q in chestionare:
        nr_experti_q, nr_raspunsuri_q, rata_q, resp_ids = get_questionnaire_rate_and_counts(
            questionnaire=q,
            scope=QuestionnaireScopeSnapshot.SCOPE_CRITERION,
            criterion=criteriu,
        )

        q.nr_experti_alocati = nr_experti_q
        q.nr_respondenti = nr_raspunsuri_q
        q.proc_respondenti = rata_q
        q.respondenti = list(User.objects.filter(id__in=resp_ids).order_by("last_name", "first_name"))

        total_raspunsuri_primite += int(nr_raspunsuri_q or 0)
        rates.append(float(rata_q or 0.0))

    rata_medie_raspuns = round((sum(rates) / len(rates)), 1) if rates else 0.0

    return render(
        request,
        "portal/admin_criteriu_dashboard.html",
        {
            "criteriu": criteriu,
            "nr_experti": nr_experti,
            "nr_chestionare": nr_chestionare,
            "nr_raspunsuri_primite": total_raspunsuri_primite,
            "rata_medie_raspuns": rata_medie_raspuns,
            "chestionare": chestionare,
        },
    )



@user_passes_test(is_internal)
def admin_export(request):
    chestionare_all = Questionnaire.objects.filter(arhivat=False).order_by("-creat_la")
    chapters_all = Chapter.objects.all().order_by("numar")
    criteria_all = Criterion.objects.all().order_by("cod")

    if request.method == "POST":
        fmt = request.POST.get("format", "csv")
        ids = request.POST.getlist("chestionare")
        ch_ids = request.POST.getlist("capitole")
        cr_ids = request.POST.getlist("criterii")
        include_general = bool(request.POST.get("general"))

        qs = Questionnaire.objects.none()
        if ids:
            qs = qs | Questionnaire.objects.filter(arhivat=False, id__in=ids)
        if ch_ids:
            qs = qs | Questionnaire.objects.filter(arhivat=False, capitole__id__in=ch_ids)
        if cr_ids:
            qs = qs | Questionnaire.objects.filter(arhivat=False, criterii__id__in=cr_ids)
        if include_general:
            qs = qs | Questionnaire.objects.filter(arhivat=False, este_general=True)
        qs = qs.distinct().order_by("-creat_la")

        if not qs.exists():
            messages.error(request, "Selectează cel puțin un chestionar sau un filtru (General/capitol/criteriu).")
            return redirect("admin_export")

        filename_base = f"raspunsuri_{timezone.now().strftime('%Y%m%d_%H%M')}"

        if fmt == "xlsx":
            content = export_xlsx(qs)
            resp = HttpResponse(
                content,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            resp["Content-Disposition"] = f'attachment; filename="{filename_base}.xlsx"'
            return resp

        if fmt == "pdf":
            content = export_pdf(qs)
            resp = HttpResponse(content, content_type="application/pdf")
            resp["Content-Disposition"] = f'attachment; filename="{filename_base}.pdf"'
            return resp

        content = export_csv(qs)
        resp = HttpResponse(content, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{filename_base}.csv"'
        return resp

    return render(
        request,
        "portal/admin_export.html",
        {"chestionare": chestionare_all, "capitole": chapters_all, "criterii": criteria_all},
    )


# -------------------- ARHIVARE (soft delete) --------------------


@user_passes_test(is_admin)
def admin_arhiva(request):
    experti_arhivati = (
        User.objects.filter(is_staff=False, is_active=False, profil_expert__arhivat=True)
        .select_related("profil_expert")
        .order_by("last_name", "first_name")
    )
    chestionare_arhivate = Questionnaire.objects.filter(arhivat=True).order_by("-arhivat_la", "-creat_la")
    return render(
        request,
        "portal/admin_arhiva.html",
        {"experti": experti_arhivati, "chestionare": chestionare_arhivate},
    )


@user_passes_test(is_admin)
def admin_expert_arhivare(request, pk: int):
    user = get_object_or_404(User, pk=pk)
    if user.is_staff:
        messages.error(request, "Acest utilizator este administrator.")
        return redirect("admin_experti_list")

    profil = _get_or_create_profile(user)

    if request.method == "POST":
        profil.arhivat = True
        profil.arhivat_la = timezone.now()
        profil.save(update_fields=["arhivat", "arhivat_la"])
        user.is_active = False
        user.save(update_fields=["is_active"])
        messages.success(request, "Expertul a fost mutat în arhivă (nu a fost șters definitiv).")
        return redirect("admin_experti_list")

    return render(
        request,
        "portal/admin_confirm.html",
        {
            "titlu": "Arhivare expert",
            "obiect": user.get_full_name() or user.username,
            "mesaj": "Acest expert va fi scos din listele principale și nu va mai putea accesa platforma. Îl vei putea restabili ulterior din Arhivă.",
            "confirm_text": "Arhivează",
            "confirm_class": "btn-danger",
            "cancel_url": request.GET.get("next") or "/administrare/experti/",
        },
    )


@user_passes_test(is_admin)
def admin_expert_restabilire(request, pk: int):
    user = get_object_or_404(User, pk=pk)
    profil = _get_or_create_profile(user)

    if request.method == "POST":
        profil.arhivat = False
        profil.arhivat_la = None
        profil.save(update_fields=["arhivat", "arhivat_la"])
        user.is_active = True
        user.save(update_fields=["is_active"])
        messages.success(request, "Expertul a fost restabilit.")
        return redirect("admin_arhiva")

    return render(
        request,
        "portal/admin_confirm.html",
        {
            "titlu": "Restabilire expert",
            "obiect": user.get_full_name() or user.username,
            "mesaj": "Expertul va reapărea în listele principale și va putea accesa din nou platforma.",
            "confirm_text": "Restabilește",
            "confirm_class": "btn-success",
            "cancel_url": request.GET.get("next") or "/administrare/arhiva/",
        },
    )


@user_passes_test(is_admin)
def admin_chestionar_arhivare(request, pk: int):
    chestionar = get_object_or_404(Questionnaire, pk=pk)

    if request.method == "POST":
        chestionar.arhivat = True
        chestionar.arhivat_la = timezone.now()
        chestionar.save(update_fields=["arhivat", "arhivat_la"])
        messages.success(request, "Chestionarul a fost mutat în arhivă (nu a fost șters definitiv).")
        return redirect("admin_chestionare_list")

    return render(
        request,
        "portal/admin_confirm.html",
        {
            "titlu": "Arhivare chestionar",
            "obiect": chestionar.titlu,
            "mesaj": "Chestionarul va fi scos din listele principale (admin și experți). Îl vei putea restabili ulterior din Arhivă.",
            "confirm_text": "Arhivează",
            "confirm_class": "btn-danger",
            "cancel_url": request.GET.get("next") or "/administrare/chestionare/",
        },
    )


@user_passes_test(is_admin)
def admin_chestionar_restabilire(request, pk: int):
    chestionar = get_object_or_404(Questionnaire, pk=pk)

    if request.method == "POST":
        chestionar.arhivat = False
        chestionar.arhivat_la = None
        chestionar.save(update_fields=["arhivat", "arhivat_la"])
        messages.success(request, "Chestionarul a fost restabilit.")
        return redirect("admin_arhiva")

    return render(
        request,
        "portal/admin_confirm.html",
        {
            "titlu": "Restabilire chestionar",
            "obiect": chestionar.titlu,
            "mesaj": "Chestionarul va reapărea în listele principale.",
            "confirm_text": "Restabilește",
            "confirm_class": "btn-success",
            "cancel_url": request.GET.get("next") or "/administrare/arhiva/",
        },
    )


# -------------------- RĂSPUNSURI (dashboard avansat) --------------------


@user_passes_test(is_internal)
def admin_chestionar_raspunsuri(request, pk: int):
    chestionar = get_object_or_404(Questionnaire, pk=pk)

    submissions = (
        Submission.objects.filter(questionnaire=chestionar)
        .filter(status=Submission.STATUS_TRIMIS)
        .select_related("expert")
        .prefetch_related("raspunsuri", "raspunsuri__question")
        .order_by("expert__last_name", "expert__first_name")
        .distinct()
    )

    experti = [s.expert for s in submissions]
    intrebari = list(chestionar.intrebari.all().order_by("ord"))

    # answers[expert_id][question_id] = text
    answers = {}
    for s in submissions:
        m = {}
        for a in s.raspunsuri.all():
            m[a.question_id] = a.text
        answers[s.expert_id] = m

    back_url = request.GET.get("back")

    return render(
        request,
        "portal/admin_chestionar_raspunsuri.html",
        {
            "chestionar": chestionar,
            "experti": experti,
            "intrebari": intrebari,
            "answers": answers,
            "back_url": back_url,
        },
    )


@user_passes_test(is_internal)
def admin_chestionar_raspunsuri_expert(request, pk: int, expert_id: int):
    chestionar = get_object_or_404(Questionnaire, pk=pk)
    expert = get_object_or_404(User, pk=expert_id)

    submission = get_object_or_404(Submission, questionnaire=chestionar, expert=expert, status=Submission.STATUS_TRIMIS)

    answers_qs = (
        Answer.objects.filter(submission=submission)
        .select_related("question", "comentarii_rezolvat_de")
        .prefetch_related("comentarii", "comentarii__author")
    )
    ans_by_qid = {a.question_id: a for a in answers_qs}

    rows = []
    for q in chestionar.intrebari.all().order_by("ord"):
        a = ans_by_qid.get(q.id)
        if not a:
            # Siguranță pentru date vechi/incomplete: asigură existența Answer.
            a, _ = Answer.objects.get_or_create(submission=submission, question=q)

        comms = list(getattr(a, "comentarii", []).all()) if hasattr(a, "comentarii") else []
        last = comms[-1] if comms else None
        modified_after_last_comment = False
        if last:
            if last.answer_updated_at_snapshot and a.updated_at:
                modified_after_last_comment = a.updated_at > last.answer_updated_at_snapshot
            elif a.updated_at:
                modified_after_last_comment = a.updated_at > last.updated_at

        rows.append(
            {
                "question": q,
                "answer": a,
                "text": a.text,
                "comments": comms,
                "thread_rezolvat": bool(getattr(a, "comentarii_rezolvat", False)),
                "thread_rezolvat_la": getattr(a, "comentarii_rezolvat_la", None),
                "thread_rezolvat_de": getattr(a, "comentarii_rezolvat_de", None),
                "answer_modified_after_last_comment": modified_after_last_comment,
            }
        )

    back_url = request.GET.get("back")

    return render(
        request,
        "portal/admin_chestionar_raspunsuri_expert.html",
        {
            "chestionar": chestionar,
            "expert": expert,
            "submission": submission,
            "rows": rows,
            "back_url": back_url,
        },
    )


def _can_manage_comment(user: User, comment: AnswerComment) -> bool:
    """Permisiuni editare/ștergere comentariu.

    - Admin: poate gestiona orice comentariu
    - Staff: doar propriile comentarii
    """
    if is_admin(user):
        return True
    if is_staff_user(user) and comment.author_id == user.id:
        return True
    return False


@user_passes_test(is_internal)
def answer_comment_create(request, answer_id: int):
    if request.method != "POST":
        raise Http404()

    answer = get_object_or_404(
        Answer.objects.select_related(
            "submission",
            "submission__questionnaire",
            "submission__expert",
            "question",
        ),
        pk=answer_id,
    )

    # Comentăm doar pe răspunsuri trimise (nu pe ciorne).
    if answer.submission.status != Submission.STATUS_TRIMIS:
        raise PermissionDenied("Nu poți comenta pe o ciornă.")

    text = (request.POST.get("text") or "").strip()
    if not text:
        messages.error(request, "Comentariul nu poate fi gol.")
    else:
        AnswerComment.objects.create(
            answer=answer,
            author=request.user,
            text=text[:2000],
        )

        # Orice comentariu nou redeschide thread-ul.
        if getattr(answer, "comentarii_rezolvat", False):
            answer.comentarii_rezolvat = False
            answer.comentarii_rezolvat_la = None
            answer.comentarii_rezolvat_de = None
            answer.save(update_fields=["comentarii_rezolvat", "comentarii_rezolvat_la", "comentarii_rezolvat_de"])

        messages.success(request, "Comentariul a fost adăugat (vizibil expertului).")

    next_url = (
        request.POST.get("next")
        or request.GET.get("next")
        or request.META.get("HTTP_REFERER")
        or "/administrare/"
    )
    return redirect(next_url)


@user_passes_test(is_internal)
def answer_comment_edit(request, pk: int):
    comment = get_object_or_404(
        AnswerComment.objects.select_related(
            "author",
            "answer",
            "answer__question",
            "answer__submission",
            "answer__submission__questionnaire",
            "answer__submission__expert",
        ),
        pk=pk,
    )

    if not _can_manage_comment(request.user, comment):
        raise PermissionDenied("Nu ai dreptul să editezi acest comentariu.")

    if request.method == "POST":
        text = (request.POST.get("text") or "").strip()
        if not text:
            messages.error(request, "Comentariul nu poate fi gol.")
        else:
            comment.text = text[:2000]
            comment.save(update_fields=["text", "updated_at"])
            messages.success(request, "Comentariul a fost actualizat.")
            next_url = (
                request.POST.get("next")
                or request.GET.get("next")
                or request.META.get("HTTP_REFERER")
                or "/administrare/"
            )
            return redirect(next_url)

    back_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or "/administrare/"
    return render(
        request,
        "portal/admin_comment_edit.html",
        {
            "comment": comment,
            "back_url": back_url,
        },
    )


@user_passes_test(is_internal)
def answer_comment_delete(request, pk: int):
    comment = get_object_or_404(
        AnswerComment.objects.select_related(
            "author",
            "answer",
            "answer__question",
            "answer__submission",
            "answer__submission__questionnaire",
            "answer__submission__expert",
        ),
        pk=pk,
    )

    if not _can_manage_comment(request.user, comment):
        raise PermissionDenied("Nu ai dreptul să ștergi acest comentariu.")

    if request.method == "POST":
        answer = comment.answer
        comment.delete()

        # Dacă nu mai există comentarii, resetăm status-ul thread-ului (opțional, dar util).
        if not AnswerComment.objects.filter(answer=answer).exists():
            if getattr(answer, "comentarii_rezolvat", False):
                answer.comentarii_rezolvat = False
                answer.comentarii_rezolvat_la = None
                answer.comentarii_rezolvat_de = None
                answer.save(update_fields=["comentarii_rezolvat", "comentarii_rezolvat_la", "comentarii_rezolvat_de"])

        messages.success(request, "Comentariul a fost șters.")
        next_url = (
            request.POST.get("next")
            or request.GET.get("next")
            or request.META.get("HTTP_REFERER")
            or "/administrare/"
        )
        return redirect(next_url)

    cancel_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or "/administrare/"
    obiect = f"{comment.answer.submission.expert.get_full_name() or comment.answer.submission.expert.username} – Întrebarea {comment.answer.question.ord}"
    mesaj = "Comentariul va fi șters definitiv. Această acțiune nu poate fi anulată."
    return render(
        request,
        "portal/confirm_delete.html",
        {
            "titlu": "Șterge comentariu",
            "obiect": obiect,
            "mesaj": mesaj,
            "confirm_text": "Șterge",
            "confirm_class": "btn-danger",
            "cancel_url": cancel_url,
        },
    )


@user_passes_test(is_internal)
def answer_thread_toggle_resolved(request, answer_id: int):
    if request.method != "POST":
        raise Http404()

    answer = get_object_or_404(
        Answer.objects.select_related(
            "submission",
            "submission__questionnaire",
            "submission__expert",
            "question",
        ),
        pk=answer_id,
    )

    if answer.submission.status != Submission.STATUS_TRIMIS:
        raise PermissionDenied("Nu poți marca rezolvat pentru o ciornă.")

    if getattr(answer, "comentarii_rezolvat", False):
        answer.comentarii_rezolvat = False
        answer.comentarii_rezolvat_la = None
        answer.comentarii_rezolvat_de = None
        answer.save(update_fields=["comentarii_rezolvat", "comentarii_rezolvat_la", "comentarii_rezolvat_de"])
        messages.info(request, "Thread-ul a fost redeschis.")
    else:
        answer.comentarii_rezolvat = True
        answer.comentarii_rezolvat_la = timezone.now()
        answer.comentarii_rezolvat_de = request.user
        answer.save(update_fields=["comentarii_rezolvat", "comentarii_rezolvat_la", "comentarii_rezolvat_de"])
        messages.success(request, "Thread-ul a fost marcat ca rezolvat.")

    next_url = (
        request.POST.get("next")
        or request.GET.get("next")
        or request.META.get("HTTP_REFERER")
        or "/administrare/"
    )
    return redirect(next_url)
