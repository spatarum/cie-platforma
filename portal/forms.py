import secrets
from typing import Dict, Tuple

from django import forms
from django.contrib.auth.models import User
from django.utils import timezone

from .models import (
    Answer,
    Chapter,
    Criterion,
    ExpertProfile,
    Question,
    Questionnaire,
    Submission,
)


class ExpertCreateForm(forms.Form):
    prenume = forms.CharField(label="Prenume", max_length=150)
    nume = forms.CharField(label="Nume", max_length=150)
    email = forms.EmailField(label="Email", max_length=254)
    telefon = forms.CharField(label="Telefon", max_length=50, required=False)
    organizatie = forms.CharField(label="Organizație / instituție", max_length=255, required=False)
    functie = forms.CharField(label="Funcție / poziție", max_length=255, required=False)
    sumar_expertiza = forms.CharField(
        label="Sumar expertiză (scurt)",
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    parola = forms.CharField(
        label="Parolă (opțional – dacă o lași goală, se generează automat)",
        required=False,
        widget=forms.PasswordInput,
    )
    confirma_parola = forms.CharField(label="Confirmă parola", required=False, widget=forms.PasswordInput)

    capitole = forms.ModelMultipleChoiceField(
        label="Capitole (domenii de expertiză)",
        queryset=Chapter.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
    )
    criterii = forms.ModelMultipleChoiceField(
        label="Criterii (domenii de expertiză)",
        queryset=Criterion.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
    )

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        if User.objects.filter(username=email).exists() or User.objects.filter(email=email).exists():
            raise forms.ValidationError("Există deja un utilizator cu acest email.")
        return email

    def clean(self):
        cleaned = super().clean()
        parola = cleaned.get("parola") or ""
        confirma = cleaned.get("confirma_parola") or ""
        if parola or confirma:
            if parola != confirma:
                self.add_error("confirma_parola", "Parolele nu coincid.")
        return cleaned

    def save(self) -> Tuple[User, str]:
        """Creează utilizator + profil. Returnează (user, parola_finală)."""
        email = self.cleaned_data["email"].lower().strip()
        parola = (self.cleaned_data.get("parola") or "").strip()
        if not parola:
            parola = secrets.token_urlsafe(10)

        user = User.objects.create_user(
            username=email,
            email=email,
            password=parola,
            first_name=self.cleaned_data.get("prenume", "").strip(),
            last_name=self.cleaned_data.get("nume", "").strip(),
        )
        user.is_staff = False
        user.is_active = True
        user.save()

        profil = getattr(user, "profil_expert", None)
        if not profil:
            profil = ExpertProfile.objects.create(user=user)

        profil.telefon = self.cleaned_data.get("telefon", "").strip()
        profil.organizatie = self.cleaned_data.get("organizatie", "").strip()
        profil.functie = self.cleaned_data.get("functie", "").strip()
        profil.sumar_expertiza = self.cleaned_data.get("sumar_expertiza", "").strip()
        profil.save()

        profil.capitole.set(self.cleaned_data.get("capitole"))
        profil.criterii.set(self.cleaned_data.get("criterii"))

        return user, parola


class ExpertUpdateForm(forms.Form):
    prenume = forms.CharField(label="Prenume", max_length=150)
    nume = forms.CharField(label="Nume", max_length=150)
    email = forms.EmailField(label="Email", max_length=254, disabled=True)
    telefon = forms.CharField(label="Telefon", max_length=50, required=False)
    organizatie = forms.CharField(label="Organizație / instituție", max_length=255, required=False)
    functie = forms.CharField(label="Funcție / poziție", max_length=255, required=False)
    sumar_expertiza = forms.CharField(
        label="Sumar expertiză (scurt)",
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    parola_noua = forms.CharField(label="Parolă nouă (opțional)", required=False, widget=forms.PasswordInput)

    capitole = forms.ModelMultipleChoiceField(
        label="Capitole (domenii de expertiză)",
        queryset=Chapter.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
    )
    criterii = forms.ModelMultipleChoiceField(
        label="Criterii (domenii de expertiză)",
        queryset=Criterion.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
    )

    def __init__(self, *args, user: User, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        profil = getattr(user, "profil_expert", None)
        self.initial.update(
            {
                "prenume": user.first_name,
                "nume": user.last_name,
                "email": user.email,
                "telefon": getattr(profil, "telefon", ""),
                "organizatie": getattr(profil, "organizatie", ""),
                "functie": getattr(profil, "functie", ""),
                "sumar_expertiza": getattr(profil, "sumar_expertiza", ""),
                "capitole": profil.capitole.all() if profil else [],
                "criterii": profil.criterii.all() if profil else [],
            }
        )

    def save(self) -> None:
        user = self.user
        user.first_name = self.cleaned_data.get("prenume", "").strip()
        user.last_name = self.cleaned_data.get("nume", "").strip()
        user.save()

        profil = getattr(user, "profil_expert", None)
        if not profil:
            profil = ExpertProfile.objects.create(user=user)

        profil.telefon = self.cleaned_data.get("telefon", "").strip()
        profil.organizatie = self.cleaned_data.get("organizatie", "").strip()
        profil.functie = self.cleaned_data.get("functie", "").strip()
        profil.sumar_expertiza = self.cleaned_data.get("sumar_expertiza", "").strip()
        profil.save()

        profil.capitole.set(self.cleaned_data.get("capitole"))
        profil.criterii.set(self.cleaned_data.get("criterii"))

        parola_noua = (self.cleaned_data.get("parola_noua") or "").strip()
        if parola_noua:
            user.set_password(parola_noua)
            user.save()


class ChestionarForm(forms.ModelForm):
    """Form de chestionar + 20 întrebări (câmpuri)"""

    for i in range(1, 21):
        locals()[f"intrebare_{i}"] = forms.CharField(
            label=f"Întrebarea {i}",
            required=False,
            max_length=1000,
            widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Scrie întrebarea aici..."}),
        )

    class Meta:
        model = Questionnaire
        fields = ["titlu", "descriere", "termen_limita", "capitole", "criterii"]
        widgets = {
            "descriere": forms.Textarea(attrs={"rows": 3}),
            "termen_limita": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "capitole": forms.CheckboxSelectMultiple(),
            "criterii": forms.CheckboxSelectMultiple(),
        }
        labels = {
            "titlu": "Titlu",
            "descriere": "Descriere (opțional)",
            "termen_limita": "Termen limită",
            "capitole": "Capitole alocate",
            "criterii": "Criterii alocate",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance: Questionnaire = kwargs.get("instance")
        if instance and instance.pk:
            # prepopulează întrebările
            qs = list(instance.intrebari.all().order_by("ord"))
            for idx, q in enumerate(qs, start=1):
                if idx <= 20:
                    self.initial[f"intrebare_{idx}"] = q.text

            # dacă există răspunsuri, blocăm editarea întrebărilor pentru a evita pierderi de date
            if instance.submisii.exists():
                for i in range(1, 21):
                    self.fields[f"intrebare_{i}"].disabled = True
                    self.fields[f"intrebare_{i}"].help_text = "Întrebările nu pot fi modificate după ce au fost primite răspunsuri."

    def clean(self):
        cleaned = super().clean()
        # validare termen (dacă e introdus ca datetime-local fără tz)
        termen = cleaned.get("termen_limita")
        if termen and timezone.is_naive(termen):
            cleaned["termen_limita"] = timezone.make_aware(termen)

        intrebari = [
            (cleaned.get(f"intrebare_{i}") or "").strip()
            for i in range(1, 21)
        ]
        intrebari = [t for t in intrebari if t]
        if not intrebari:
            raise forms.ValidationError("Adaugă cel puțin o întrebare.")
        if len(intrebari) > 20:
            raise forms.ValidationError("Un chestionar poate avea maximum 20 de întrebări.")
        return cleaned

    def save(self, commit=True, user=None):
        questionnaire: Questionnaire = super().save(commit=False)
        if user and not questionnaire.pk:
            questionnaire.creat_de = user
        if commit:
            questionnaire.save()
            self.save_m2m()

            # Dacă întrebările sunt editabile, refacem lista
            if not questionnaire.submisii.exists():
                questionnaire.intrebari.all().delete()
                ord_no = 1
                for i in range(1, 21):
                    text = (self.cleaned_data.get(f"intrebare_{i}") or "").strip()
                    if text:
                        Question.objects.create(questionnaire=questionnaire, ord=ord_no, text=text)
                        ord_no += 1

        return questionnaire


class RaspunsChestionarForm(forms.Form):
    def __init__(self, *args, questionnaire: Questionnaire, submission: Submission, **kwargs):
        super().__init__(*args, **kwargs)
        self.questionnaire = questionnaire
        self.submission = submission

        existing: Dict[int, str] = {
            a.question_id: a.text for a in Answer.objects.filter(submission=submission)
        }

        for q in questionnaire.intrebari.all().order_by("ord"):
            field_name = f"q_{q.id}"
            self.fields[field_name] = forms.CharField(
                label=f"{q.ord}. {q.text}",
                required=False,
                max_length=1500,
                widget=forms.Textarea(attrs={
                    "rows": 4,
                    "maxlength": "1500",
                    "placeholder": "Răspuns (max. 1500 caractere)",
                }),
            )
            self.initial[field_name] = existing.get(q.id, "")

    def save(self) -> None:
        for q in self.questionnaire.intrebari.all():
            field_name = f"q_{q.id}"
            text = (self.cleaned_data.get(field_name) or "").strip()
            answer, _ = Answer.objects.get_or_create(submission=self.submission, question=q)
            answer.text = text[:1500]
            answer.save()
