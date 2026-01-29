from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .exports import export_csv, export_pdf, export_xlsx
from .forms import ChestionarForm, ExpertCreateForm, ExpertUpdateForm, RaspunsChestionarForm
from .models import Chapter, Criterion, ExpertProfile, Questionnaire, Submission
from .utils import group_chapters_by_cluster


def is_admin(user: User) -> bool:
    return user.is_authenticated and user.is_staff


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
        Questionnaire.objects.filter(
            Q(capitole__in=profil.capitole.all()) | Q(criterii__in=profil.criterii.all())
        )
        .distinct()
        .order_by("termen_limita")
    )


def _expert_can_access(user: User, chestionar: Questionnaire) -> bool:
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
    qs = _expert_accessible_qs(request.user)
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
        },
    )


@user_passes_test(is_expert)
def expert_profile(request):
    profil = _get_or_create_profile(request.user)
    return render(request, "portal/expert_profile.html", {"profil": profil})


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

    return render(
        request,
        "portal/expert_chestionar.html",
        {
            "chestionar": chestionar,
            "form": form,
            "submission": submission,
            "editabil": editabil,
        },
    )


# -------------------- ADMIN --------------------


@user_passes_test(is_admin)
def admin_dashboard(request):
    chestionare = Questionnaire.objects.all().order_by("-creat_la")[:10]
    experti = User.objects.filter(is_staff=False).count()
    return render(
        request,
        "portal/admin_dashboard.html",
        {"chestionare": chestionare, "nr_experti": experti},
    )


@user_passes_test(is_admin)
def admin_questionnaire_list(request):
    chestionare = Questionnaire.objects.all().order_by("-creat_la")
    return render(request, "portal/admin_chestionare_list.html", {"chestionare": chestionare})


@user_passes_test(is_admin)
def admin_questionnaire_create(request):
    if request.method == "POST":
        form = ChestionarForm(request.POST)
        if form.is_valid():
            chestionar = form.save(user=request.user)
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


@user_passes_test(is_admin)
def admin_questionnaire_edit(request, pk: int):
    chestionar = get_object_or_404(Questionnaire, pk=pk)

    if request.method == "POST":
        form = ChestionarForm(request.POST, instance=chestionar)
        if form.is_valid():
            form.save(user=request.user)
            messages.success(request, "Chestionarul a fost actualizat.")
            return redirect("admin_chestionar_edit", pk=pk)
    else:
        form = ChestionarForm(instance=chestionar)

    question_fields = [form[f"intrebare_{i}"] for i in range(1, 21)]

    total_raspunsuri = chestionar.submisii.count()
    trimise = chestionar.submisii.filter(status=Submission.STATUS_TRIMIS).count()

    return render(
        request,
        "portal/admin_chestionar_form.html",
        {
            "form": form,
            "chestionar": chestionar,
            "titlu_pagina": "Editare chestionar",
            "total_raspunsuri": total_raspunsuri,
            "trimise": trimise,
            "question_fields": question_fields,
        },
    )


@user_passes_test(is_admin)
def admin_expert_list(request):
    experti = User.objects.filter(is_staff=False).order_by("last_name", "first_name")
    return render(request, "portal/admin_experti_list.html", {"experti": experti})


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


@user_passes_test(is_admin)
def admin_expert_edit(request, pk: int):
    user = get_object_or_404(User, pk=pk)
    if user.is_staff:
        messages.error(request, "Acest utilizator este administrator.")
        return redirect("admin_experti_list")

    if request.method == "POST":
        form = ExpertUpdateForm(request.POST, user=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profilul expertului a fost actualizat.")
            return redirect("admin_expert_edit", pk=pk)
    else:
        form = ExpertUpdateForm(user=user)

    profil = _get_or_create_profile(user)

    return render(
        request,
        "portal/admin_expert_form.html",
        {"form": form, "titlu_pagina": "Editare expert", "expert_user": user, "profil": profil},
    )


@user_passes_test(is_admin)
def admin_referinte(request):
    grouped = group_chapters_by_cluster()
    criterii = Criterion.objects.all().order_by("cod")
    return render(
        request,
        "portal/admin_referinte.html",
        {"grouped": grouped, "criterii": criterii},
    )




@user_passes_test(is_admin)
def admin_capitol_dashboard(request, pk: int):
    capitol = get_object_or_404(Chapter, pk=pk)

    expert_ids_qs = (
        User.objects.filter(is_staff=False, profil_expert__capitole=capitol)
        .values_list("id", flat=True)
        .distinct()
    )
    expert_ids = list(expert_ids_qs)
    nr_experti = len(expert_ids)

    chestionare_qs = Questionnaire.objects.filter(capitole=capitol).distinct().order_by("-creat_la")
    chestionare = list(chestionare_qs)
    nr_chestionare = len(chestionare)

    # Per chestionar: câți experți (din cei alocați capitolului) au răspuns (trimis sau cel puțin un răspuns completat)
    for q in chestionare:
        if nr_experti == 0:
            q.nr_respondenti = 0
            q.proc_respondenti = 0
            continue

        nr_resp = (
            Submission.objects.filter(questionnaire=q, expert_id__in=expert_ids)
            .filter(Q(status=Submission.STATUS_TRIMIS) | Q(raspunsuri__text__gt=""))
            .values("expert_id")
            .distinct()
            .count()
        )
        q.nr_respondenti = nr_resp
        q.proc_respondenti = round((nr_resp / nr_experti) * 100, 1)

    # La nivel de capitol: experți unici care au răspuns la cel puțin un chestionar din acest capitol
    if nr_experti and nr_chestionare:
        nr_experti_care_au_raspuns = (
            Submission.objects.filter(questionnaire__in=chestionare, expert_id__in=expert_ids)
            .filter(Q(status=Submission.STATUS_TRIMIS) | Q(raspunsuri__text__gt=""))
            .values("expert_id")
            .distinct()
            .count()
        )
    else:
        nr_experti_care_au_raspuns = 0

    rata_raspuns = round((nr_experti_care_au_raspuns / nr_experti) * 100, 1) if nr_experti else 0

    return render(
        request,
        "portal/admin_capitol_dashboard.html",
        {
            "capitol": capitol,
            "nr_experti": nr_experti,
            "nr_chestionare": nr_chestionare,
            "nr_experti_care_au_raspuns": nr_experti_care_au_raspuns,
            "rata_raspuns": rata_raspuns,
            "chestionare": chestionare,
        },
    )
@user_passes_test(is_admin)
def admin_export(request):
    chestionare_all = Questionnaire.objects.all().order_by("-creat_la")
    chapters_all = Chapter.objects.all().order_by("numar")
    criteria_all = Criterion.objects.all().order_by("cod")

    if request.method == "POST":
        fmt = request.POST.get("format", "csv")
        ids = request.POST.getlist("chestionare")
        ch_ids = request.POST.getlist("capitole")
        cr_ids = request.POST.getlist("criterii")

        qs = Questionnaire.objects.none()
        if ids:
            qs = qs | Questionnaire.objects.filter(id__in=ids)
        if ch_ids:
            qs = qs | Questionnaire.objects.filter(capitole__id__in=ch_ids)
        if cr_ids:
            qs = qs | Questionnaire.objects.filter(criterii__id__in=cr_ids)
        qs = qs.distinct().order_by("-creat_la")

        if not qs.exists():
            messages.error(request, "Selectează cel puțin un chestionar sau un filtru (capitol/criteriu).")
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
