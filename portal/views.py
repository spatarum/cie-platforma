from __future__ import annotations

import csv
import io
import secrets
import re
from datetime import datetime, timedelta

import openpyxl

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q, Count
from django.db.models.functions import Coalesce
from django.forms import formset_factory
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
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
def admin_referinte(request):
    grouped = group_chapters_by_cluster()
    criterii = Criterion.objects.all().order_by("cod")
    return render(
        request,
        "portal/admin_referinte.html",
        {"grouped": grouped, "criterii": criterii},
    )


# -------------------- PNA (admin) --------------------


@user_passes_test(is_admin)
def admin_pna_list(request):
    """Pagina PNA (admin): listă + tabel structurat pe capitole/foi de parcurs."""

    q = (request.GET.get("q") or "").strip()

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

    proiecte = list(proiecte_qs)

    # Statistici rapide (dashboard)
    today = timezone.localdate()

    def _gov_overdue(p: PnaProject) -> bool:
        d = p.termen_guvern_efectiv
        return bool(d and d < today)

    def _parl_overdue(p: PnaProject) -> bool:
        d = p.termen_aprobare_parlament
        return bool(d and d < today)

    total = len(proiecte)
    nr_overdue = sum(1 for p in proiecte if (_gov_overdue(p) or _parl_overdue(p)))
    nr_fara_termene = sum(
        1
        for p in proiecte
        if not p.termen_guvern_efectiv and not p.termen_aprobare_parlament
    )

    # Upcoming (următoarele 60 zile) pe termenul "cel mai apropiat" dintre Guvern/Parlament
    def _next_deadline(p: PnaProject):
        cands = [d for d in [p.termen_guvern_efectiv, p.termen_aprobare_parlament] if d]
        return min(cands) if cands else None

    upcoming_60 = [p for p in proiecte if _next_deadline(p) and today <= _next_deadline(p) <= (today + timedelta(days=60))]
    nr_upcoming_60 = len(upcoming_60)

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
            "total": total,
            "nr_overdue": nr_overdue,
            "nr_upcoming_60": nr_upcoming_60,
            "nr_fara_termene": nr_fara_termene,
            "criterii_groups": criterii_groups,
            "chapter_groups": chapter_groups,
        },
    )


@user_passes_test(is_admin)
def admin_pna_dashboard(request):
    """Dashboard PNA (admin).

    Cerințe:
      - total proiecte în sistem + distribuție pe status (nr + %), afișat ca grafic tip coloane
      - matrice: pe rânduri (foi de parcurs + capitole), pe coloane luni (pentru anul selectat)
        – în celule: număr proiecte cu deadline în luna respectivă.

    Deadline pentru matrice: termen actualizat Guvern (dacă există), altfel termen Parlament.
    """

    proiecte = list(
        PnaProject.objects.filter(arhivat=False)
        .select_related("chapter", "criterion", "institutie_principala_ref")
        .prefetch_related("institutii_responsabile")
        .order_by("-actualizat_la")
    )

    total = len(proiecte)

    # -------------------- distribuție pe status --------------------
    counts = {code: 0 for code, _ in PnaProject.STATUS_IMPLEMENTARE_CHOICES}
    for p in proiecte:
        counts[p.status_implementare] = counts.get(p.status_implementare, 0) + 1

    status_rows = []
    for code, label in PnaProject.STATUS_IMPLEMENTARE_CHOICES:
        nr = counts.get(code, 0)
        pct = round((nr / total) * 100, 1) if total else 0.0
        status_rows.append({"code": code, "label": label, "nr": nr, "pct": pct, "pct_int": int(round(pct))})

    # -------------------- matrice pe luni --------------------
    today = timezone.localdate()
    years = sorted({p.termen_deadline.year for p in proiecte if p.termen_deadline})
    selected_year = None
    try:
        selected_year = int(request.GET.get("year") or 0)
    except Exception:
        selected_year = None
    if not selected_year:
        selected_year = today.year
    if years and selected_year not in years:
        # dacă anul nu există în date, alegem cel mai apropiat (ultimul)
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
        if key[1]:
            matrix[key][d.month] += 1

    criterii_rows = []
    criterii = list(Criterion.objects.all().order_by("cod"))
    for cr in criterii:
        counts_by_month = [matrix[("CR", cr.id)].get(m, 0) for m, _ in months]
        if sum(counts_by_month) == 0:
            continue
        criterii_rows.append({"obj": cr, "counts": counts_by_month, "total": sum(counts_by_month)})

    chapter_cluster_rows = []
    grouped_chapters = group_chapters_by_cluster()
    for cl, chapters in grouped_chapters:
        rows = []
        for ch in chapters:
            counts_by_month = [matrix[("CH", ch.id)].get(m, 0) for m, _ in months]
            if sum(counts_by_month) == 0:
                continue
            rows.append({"obj": ch, "counts": counts_by_month, "total": sum(counts_by_month)})
        if rows:
            chapter_cluster_rows.append({"cluster": cl, "rows": rows})

    # -------------------- liste utile (opțional) --------------------
    overdue = [p for p in proiecte if (p.termen_deadline and p.termen_deadline < today)]
    upcoming = [
        p
        for p in proiecte
        if (p.termen_deadline and today <= p.termen_deadline <= (today + timedelta(days=90)))
    ]
    overdue.sort(key=lambda p: (p.termen_deadline or datetime.max.date(), p.titlu))
    upcoming.sort(key=lambda p: (p.termen_deadline or datetime.max.date(), p.titlu))

    return render(
        request,
        "portal/admin_pna_dashboard.html",
        {
            "total": total,
            "status_rows": status_rows,
            "years": years or [selected_year],
            "selected_year": selected_year,
            "months": months,
            "criterii_rows": criterii_rows,
            "chapter_cluster_rows": chapter_cluster_rows,
            "nr_overdue": len(overdue),
            "nr_upcoming_90": len(upcoming),
            "overdue": overdue[:50],
            "upcoming": upcoming[:50],
        },
    )


@user_passes_test(is_admin)
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


@user_passes_test(is_admin)
def admin_pna_edit(request, pk: int):
    obj = get_object_or_404(PnaProject, pk=pk)

    ActeFormSet = formset_factory(PnaEUActInlineForm, extra=1, can_delete=True)

    # Legături existente (pentru pre-populare și update/delete)
    existing_links = list(obj.acte_ue_legaturi.select_related("eu_act").all())
    existing_by_id = {l.id: l for l in existing_links}

    if request.method == "POST":
        form = PnaProjectForm(request.POST, instance=obj)
        acte_formset = ActeFormSet(request.POST, prefix="acts")
        if form.is_valid() and acte_formset.is_valid():
            obj = form.save()
            form.sync_institution_legacy_fields(obj)

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


@user_passes_test(is_admin)
def admin_pna_detail(request, pk: int):
    obj = get_object_or_404(
        PnaProject.objects.select_related("chapter", "criterion", "institutie_principala_ref")
        .prefetch_related("acte_ue_legaturi__eu_act")
        .prefetch_related("institutii_responsabile"),
        pk=pk,
    )

    if request.method == "POST":
        attach_form = PnaEUActAttachForm(request.POST)
        if attach_form.is_valid():
            celex = attach_form.cleaned_data["celex"]
            den = (attach_form.cleaned_data.get("denumire") or "").strip()
            tip_doc = (attach_form.cleaned_data.get("tip_document") or "").strip()
            url = (attach_form.cleaned_data.get("url") or "").strip()
            tip_transp = (attach_form.cleaned_data.get("tip_transpunere") or "").strip()

            act, created = EUAct.objects.get_or_create(
                celex=celex,
                defaults={"denumire": den or celex, "tip_document": tip_doc, "url": url},
            )
            # update fields if provided
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

            link, _ = PnaProjectEUAct.objects.get_or_create(project=obj, eu_act=act)
            if tip_transp and link.tip_transpunere != tip_transp:
                link.tip_transpunere = tip_transp
                link.save(update_fields=["tip_transpunere"])

            messages.success(request, "Actul UE a fost atașat proiectului.")
            return redirect("admin_pna_detail", pk=obj.pk)
    else:
        attach_form = PnaEUActAttachForm()

    return render(
        request,
        "portal/admin_pna_detail.html",
        {
            "obj": obj,
            "attach_form": attach_form,
        },
    )


@user_passes_test(is_admin)
def admin_pna_detach_act(request, pk: int):
    link = get_object_or_404(PnaProjectEUAct, pk=pk)
    project_id = link.project_id
    if request.method == "POST":
        link.delete()
        messages.success(request, "Actul UE a fost scos din proiect.")
    return redirect("admin_pna_detail", pk=project_id)


@user_passes_test(is_admin)
def admin_pna_arhivare(request, pk: int):
    obj = get_object_or_404(PnaProject, pk=pk)
    if request.method == "POST":
        obj.arhivat = True
        obj.arhivat_la = timezone.now()
        obj.save(update_fields=["arhivat", "arhivat_la"])
        messages.success(request, "Proiectul PNA a fost arhivat.")
        return redirect("admin_pna_list")
    return redirect("admin_pna_detail", pk=obj.pk)


@user_passes_test(is_admin)
def admin_pna_restabilire(request, pk: int):
    obj = get_object_or_404(PnaProject, pk=pk)
    if request.method == "POST":
        obj.arhivat = False
        obj.arhivat_la = None
        obj.save(update_fields=["arhivat", "arhivat_la"])
        messages.success(request, "Proiectul PNA a fost restabilit.")
        return redirect("admin_pna_detail", pk=obj.pk)
    return redirect("admin_pna_detail", pk=obj.pk)


@user_passes_test(is_admin)
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
        },
    )


@user_passes_test(is_admin)
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


@user_passes_test(is_admin)
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


@user_passes_test(is_admin)
def admin_pna_scope_list(request):
    """Listă proiecte filtrată (folosită din dashboard – click pe matrice)."""

    chapter_id = request.GET.get("chapter")
    criterion_id = request.GET.get("criterion")
    year = request.GET.get("year")
    month = request.GET.get("month")

    if not chapter_id and not criterion_id:
        raise Http404("Lipsește filtrul (chapter/criterion).")

    qs = (
        PnaProject.objects.filter(arhivat=False)
        .select_related("chapter", "criterion", "institutie_principala_ref")
        .prefetch_related("institutii_responsabile")
    )

    scope_label = ""
    if chapter_id:
        ch = get_object_or_404(Chapter, pk=int(chapter_id))
        qs = qs.filter(chapter=ch)
        scope_label = f"Cap. {ch.numar} — {ch.denumire}"
    else:
        cr = get_object_or_404(Criterion, pk=int(criterion_id))
        qs = qs.filter(criterion=cr)
        scope_label = f"{cr.cod} — {cr.denumire}"

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

    return render(
        request,
        "portal/admin_pna_scope_list.html",
        {
            "scope_label": scope_label,
            "projects": projects,
            "year": year_i,
            "month": month_i,
        },
    )


@user_passes_test(is_admin)
def admin_pna_import_template_download(request):
    data = build_pna_import_template_bytes()
    response = HttpResponse(
        data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="template_import_pna.xlsx"'
    return response


@user_passes_test(is_admin)
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
