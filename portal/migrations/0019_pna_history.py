from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


def backfill_pna_history(apps, schema_editor):
    """Creează intrări inițiale în istoricul PNA pentru proiectele existente.

    - status_history: o intrare de bază ("" → status curent) la data creării proiectului
    - deadline_history: o intrare pentru fiecare termen existent la data creării proiectului

    Notă: nu încercăm să reconstruim istoric real (nu există date), ci doar să avem baseline.
    """

    PnaProject = apps.get_model("portal", "PnaProject")
    PnaProjectStatusHistory = apps.get_model("portal", "PnaProjectStatusHistory")
    PnaProjectDeadlineHistory = apps.get_model("portal", "PnaProjectDeadlineHistory")

    for p in PnaProject.objects.all().iterator():
        base_dt = getattr(p, "creat_la", None) or getattr(p, "actualizat_la", None) or timezone.now()

        # status baseline
        if not PnaProjectStatusHistory.objects.filter(project_id=p.id).exists():
            PnaProjectStatusHistory.objects.create(
                project_id=p.id,
                from_status="",
                to_status=getattr(p, "status_implementare", "") or "",
                changed_by_id=None,
                changed_at=base_dt,
                source="SYSTEM",
                note="Baseline (backfill)",
            )

        # termene baseline
        def _ensure_deadline(field_name: str, value):
            if value is None:
                return
            if PnaProjectDeadlineHistory.objects.filter(project_id=p.id, field=field_name).exists():
                return
            PnaProjectDeadlineHistory.objects.create(
                project_id=p.id,
                field=field_name,
                old_value=None,
                new_value=value,
                changed_by_id=None,
                changed_at=base_dt,
                source="SYSTEM",
                note="Baseline (backfill)",
            )

        _ensure_deadline("termen_aprobare_guvern", getattr(p, "termen_aprobare_guvern", None))
        _ensure_deadline("termen_aprobare_parlament", getattr(p, "termen_aprobare_parlament", None))
        _ensure_deadline(
            "termen_actualizat_aprobare_guvern",
            getattr(p, "termen_actualizat_aprobare_guvern", None),
        )
        _ensure_deadline(
            "consultari_publice_parlament",
            getattr(p, "consultari_publice_parlament", None),
        )


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0018_pna_statuses_and_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="PnaProjectStatusHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("from_status", models.CharField(blank=True, max_length=40)),
                ("to_status", models.CharField(blank=True, db_index=True, max_length=40)),
                ("changed_at", models.DateTimeField(db_index=True, default=timezone.now)),
                (
                    "source",
                    models.CharField(
                        choices=[("UI", "Interfață"), ("IMPORT", "Import"), ("SYSTEM", "Sistem")],
                        default="UI",
                        max_length=20,
                    ),
                ),
                ("note", models.CharField(blank=True, max_length=500)),
                (
                    "changed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="pna_status_changes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="status_history",
                        to="portal.pnaproject",
                    ),
                ),
            ],
            options={
                "verbose_name": "Istoric status PNA",
                "verbose_name_plural": "Istoric status PNA",
                "ordering": ["-changed_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="PnaProjectDeadlineHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "field",
                    models.CharField(
                        choices=[
                            ("termen_aprobare_guvern", "Termen aprobare în Guvern"),
                            ("termen_aprobare_parlament", "Termen aprobare în Parlament"),
                            ("termen_actualizat_aprobare_guvern", "Termen actualizat aprobare în Guvern"),
                            ("consultari_publice_parlament", "Consultări publice în Parlament"),
                        ],
                        db_index=True,
                        max_length=60,
                    ),
                ),
                ("old_value", models.DateField(blank=True, null=True)),
                ("new_value", models.DateField(blank=True, null=True)),
                ("changed_at", models.DateTimeField(db_index=True, default=timezone.now)),
                (
                    "source",
                    models.CharField(
                        choices=[("UI", "Interfață"), ("IMPORT", "Import"), ("SYSTEM", "Sistem")],
                        default="UI",
                        max_length=20,
                    ),
                ),
                ("note", models.CharField(blank=True, max_length=500)),
                (
                    "changed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="pna_deadline_changes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deadline_history",
                        to="portal.pnaproject",
                    ),
                ),
            ],
            options={
                "verbose_name": "Istoric termene PNA",
                "verbose_name_plural": "Istoric termene PNA",
                "ordering": ["-changed_at", "-id"],
            },
        ),
        migrations.RunPython(backfill_pna_history, migrations.RunPython.noop),
    ]
