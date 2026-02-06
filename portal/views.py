from __future__ import annotations

import csv
import io
import secrets
import re
from datetime import datetime

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .exports import export_csv, export_pdf, export_xlsx
from .forms import (
    ChestionarForm,
    ExpertCreateForm,
    ExpertUpdateForm,
    ExpertImportCSVForm,
    QuestionnaireImportCSVForm,
    RaspunsChestionarForm,
    ExpertPreferinteForm,
)
from .models import Answer, Chapter, Criterion, ExpertProfile, ImportRun, Question, Questionnaire, Submission
from .notifications import send_new_questionnaire_emails
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
    chestionare = Questionnaire.objects.filter(arhivat=False).order_by("-creat_la")[:10]
    experti = User.objects.filter(is_staff=False, is_active=True).count()
    return render(
        request,
        "portal/admin_dashboard.html",
        {"chestionare": chestionare, "nr_experti": experti},
    )

@user_passes_test(is_admin)
def admin_questionnaire_list(request):
    chestionare = Questionnaire.objects.filter(arhivat=False).order_by("-creat_la")
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
    experti = User.objects.filter(is_staff=False, is_active=True).order_by("last_name", "first_name")
    return render(request, "portal/admin_experti_list.html", {"experti": experti})


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

                            # actualizăm întrebările doar dacă nu există răspunsuri
                            if not q.submisii.exists():
                                q.intrebari.all().delete()
                                for ord_no, t in enumerate(intrebari, start=1):
                                    Question.objects.create(questionnaire=q, ord=ord_no, text=t)
                                msg = "Actualizat (întrebări înlocuite)"
                            else:
                                msg = "Actualizat (întrebările nu au fost modificate – există răspunsuri)"

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
    kind_slug = "experti" if run.kind == ImportRun.KIND_EXPERTI else ("chestionare" if run.kind == ImportRun.KIND_CHESTIONARE else "import")
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

    for q in chestionare:
        if nr_experti == 0:
            q.nr_respondenti = 0
            q.proc_respondenti = 0
            q.respondenti = []
            continue

        resp_ids_qs = (
            Submission.objects.filter(questionnaire=q, expert_id__in=expert_ids)
            .filter(Q(status=Submission.STATUS_TRIMIS) | Q(raspunsuri__text__gt=""))
            .values_list("expert_id", flat=True)
            .distinct()
        )
        resp_ids = list(resp_ids_qs)
        q.nr_respondenti = len(resp_ids)
        q.proc_respondenti = round((q.nr_respondenti / nr_experti) * 100, 1)
        q.respondenti = list(User.objects.filter(id__in=resp_ids).order_by("last_name", "first_name"))

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
        "portal/admin_general_dashboard.html",
        {
            "nr_experti": nr_experti,
            "nr_chestionare": nr_chestionare,
            "nr_experti_care_au_raspuns": nr_experti_care_au_raspuns,
            "rata_raspuns": rata_raspuns,
            "chestionare": chestionare,
        },
    )




@user_passes_test(is_admin)
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

    # Per chestionar: respondenti + procent
    for q in chestionare:
        if nr_experti == 0:
            q.nr_respondenti = 0
            q.proc_respondenti = 0
            q.respondenti = []
            continue

        resp_ids_qs = (
            Submission.objects.filter(questionnaire=q, expert_id__in=expert_ids)
            .filter(Q(status=Submission.STATUS_TRIMIS) | Q(raspunsuri__text__gt=""))
            .values_list("expert_id", flat=True)
            .distinct()
        )
        resp_ids = list(resp_ids_qs)
        q.nr_respondenti = len(resp_ids)
        q.proc_respondenti = round((q.nr_respondenti / nr_experti) * 100, 1)
        q.respondenti = list(User.objects.filter(id__in=resp_ids).order_by("last_name", "first_name"))

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

    for q in chestionare:
        if nr_experti == 0:
            q.nr_respondenti = 0
            q.proc_respondenti = 0
            q.respondenti = []
            continue

        resp_ids_qs = (
            Submission.objects.filter(questionnaire=q, expert_id__in=expert_ids)
            .filter(Q(status=Submission.STATUS_TRIMIS) | Q(raspunsuri__text__gt=""))
            .values_list("expert_id", flat=True)
            .distinct()
        )
        resp_ids = list(resp_ids_qs)
        q.nr_respondenti = len(resp_ids)
        q.proc_respondenti = round((q.nr_respondenti / nr_experti) * 100, 1)
        q.respondenti = list(User.objects.filter(id__in=resp_ids).order_by("last_name", "first_name"))

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
        "portal/admin_criteriu_dashboard.html",
        {
            "criteriu": criteriu,
            "nr_experti": nr_experti,
            "nr_chestionare": nr_chestionare,
            "nr_experti_care_au_raspuns": nr_experti_care_au_raspuns,
            "rata_raspuns": rata_raspuns,
            "chestionare": chestionare,
        },
    )



@user_passes_test(is_admin)
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


@user_passes_test(is_admin)
def admin_chestionar_raspunsuri(request, pk: int):
    chestionar = get_object_or_404(Questionnaire, pk=pk)

    submissions = (
        Submission.objects.filter(questionnaire=chestionar)
        .filter(Q(status=Submission.STATUS_TRIMIS) | Q(raspunsuri__text__gt=""))
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


@user_passes_test(is_admin)
def admin_chestionar_raspunsuri_expert(request, pk: int, expert_id: int):
    chestionar = get_object_or_404(Questionnaire, pk=pk)
    expert = get_object_or_404(User, pk=expert_id)

    submission = get_object_or_404(Submission, questionnaire=chestionar, expert=expert)

    # Map question_id -> answer
    ans_map = {
        a.question_id: a.text
        for a in Answer.objects.filter(submission=submission).select_related("question")
    }

    rows = []
    for q in chestionar.intrebari.all().order_by("ord"):
        rows.append({"question": q, "text": ans_map.get(q.id, "")})

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
