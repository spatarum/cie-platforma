"""Utilitare pentru statistici (răspunsuri / rate) în platformă.

Acest modul centralizează logica pentru:
- calculul ratelor per chestionar (în funcție de scope: General / Capitol / Criteriu);
- înghețarea ratelor la închiderea chestionarelor (snapshot), astfel încât adăugarea ulterioară
  de experți să nu modifice procentele pentru chestionarele deja închise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from .models import (
    Chapter,
    Criterion,
    Questionnaire,
    QuestionnaireScopeSnapshot,
    Submission,
)


def _eligible_experts_qs_for_scope(
    scope: str,
    chapter: Chapter | None = None,
    criterion: Criterion | None = None,
):
    """Returnează queryset cu experții eligibili pentru un scope."""

    qs = User.objects.filter(is_staff=False, is_active=True)

    if scope == QuestionnaireScopeSnapshot.SCOPE_GENERAL:
        return qs
    if scope == QuestionnaireScopeSnapshot.SCOPE_CHAPTER:
        if not chapter:
            raise ValueError("chapter este obligatoriu pentru scope CHAPTER")
        return qs.filter(profil_expert__capitole=chapter)
    if scope == QuestionnaireScopeSnapshot.SCOPE_CRITERION:
        if not criterion:
            raise ValueError("criterion este obligatoriu pentru scope CRITERION")
        return qs.filter(profil_expert__criterii=criterion)

    raise ValueError("Scope invalid")


def compute_current_questionnaire_stats(
    questionnaire: Questionnaire,
    scope: str,
    chapter: Chapter | None = None,
    criterion: Criterion | None = None,
):
    """Calculează (dinamic) nr. experți eligibili + nr. răspunsuri trimise + respondent IDs.

    Folosit pentru chestionarele deschise (unde denominatorul poate varia).
    """

    elig_qs = _eligible_experts_qs_for_scope(scope=scope, chapter=chapter, criterion=criterion).distinct()
    nr_experti = elig_qs.count()

    resp_ids = list(
        Submission.objects.filter(
            questionnaire=questionnaire,
            status=Submission.STATUS_TRIMIS,
            expert_id__in=elig_qs.values_list("id", flat=True),
        )
        .values_list("expert_id", flat=True)
        .distinct()
    )
    nr_raspunsuri = len(resp_ids)
    return nr_experti, nr_raspunsuri, resp_ids


@transaction.atomic
def ensure_scope_snapshot(
    questionnaire: Questionnaire,
    scope: str,
    chapter: Chapter | None = None,
    criterion: Criterion | None = None,
) -> QuestionnaireScopeSnapshot:
    """Returnează snapshot-ul pentru un (questionnaire, scope). Creează dacă lipsește.

    IMPORTANT: Snapshot-ul este destinat chestionarelor închise.
    Îl creăm folosind alocările *curente* la momentul creării.
    Pentru a preveni drift-ul când se schimbă alocările după închidere, avem și semnale (m2m_changed)
    care creează snapshot-uri înainte de modificarea alocărilor.
    """

    scope_key = QuestionnaireScopeSnapshot.make_scope_key(
        scope,
        chapter_id=chapter.id if chapter else None,
        criterion_id=criterion.id if criterion else None,
    )

    existing = (
        QuestionnaireScopeSnapshot.objects.select_for_update()
        .filter(questionnaire=questionnaire, scope_key=scope_key)
        .first()
    )
    if existing:
        return existing

    nr_experti, nr_raspunsuri, resp_ids = compute_current_questionnaire_stats(
        questionnaire=questionnaire,
        scope=scope,
        chapter=chapter,
        criterion=criterion,
    )

    snap = QuestionnaireScopeSnapshot.objects.create(
        questionnaire=questionnaire,
        scope=scope,
        scope_key=scope_key,
        chapter=chapter,
        criterion=criterion,
        frozen_for_deadline=questionnaire.termen_limita,
        nr_experti=nr_experti,
        nr_raspunsuri=nr_raspunsuri,
        respondent_ids=resp_ids,
    )
    return snap


def get_questionnaire_rate_and_counts(
    questionnaire: Questionnaire,
    scope: str,
    chapter: Chapter | None = None,
    criterion: Criterion | None = None,
):
    """Întoarce (nr_experti, nr_raspunsuri, rata, respondent_ids) pentru un chestionar.

    - dacă chestionarul este închis => folosește snapshot (înghețat)
    - dacă este deschis => calculează dinamic
    """

    now = timezone.now()
    if questionnaire.termen_limita < now:
        snap = ensure_scope_snapshot(questionnaire, scope=scope, chapter=chapter, criterion=criterion)
        return snap.nr_experti, snap.nr_raspunsuri, snap.rata, list(snap.respondent_ids or [])

    nr_experti, nr_raspunsuri, resp_ids = compute_current_questionnaire_stats(
        questionnaire=questionnaire,
        scope=scope,
        chapter=chapter,
        criterion=criterion,
    )
    rata = round((nr_raspunsuri / nr_experti) * 100, 1) if nr_experti else 0.0
    return nr_experti, nr_raspunsuri, rata, resp_ids


def freeze_closed_questionnaires_for_chapters(chapter_ids: Iterable[int]) -> None:
    """Asigură snapshot pentru toate chestionarele ÎNCHISE din capitolele date."""

    ids = [int(i) for i in (chapter_ids or [])]
    if not ids:
        return

    now = timezone.now()
    for ch in Chapter.objects.filter(id__in=ids):
        qs = (
            Questionnaire.objects.filter(arhivat=False, capitole=ch, termen_limita__lt=now)
            .distinct()
            .only("id", "termen_limita")
        )
        for q in qs:
            ensure_scope_snapshot(q, scope=QuestionnaireScopeSnapshot.SCOPE_CHAPTER, chapter=ch)


def freeze_closed_questionnaires_for_criteria(criterion_ids: Iterable[int]) -> None:
    """Asigură snapshot pentru toate chestionarele ÎNCHISE din criteriile date."""

    ids = [int(i) for i in (criterion_ids or [])]
    if not ids:
        return

    now = timezone.now()
    for cr in Criterion.objects.filter(id__in=ids):
        qs = (
            Questionnaire.objects.filter(arhivat=False, criterii=cr, termen_limita__lt=now)
            .distinct()
            .only("id", "termen_limita")
        )
        for q in qs:
            ensure_scope_snapshot(q, scope=QuestionnaireScopeSnapshot.SCOPE_CRITERION, criterion=cr)
