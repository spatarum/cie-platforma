import secrets
from typing import Dict, Tuple

from django import forms
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone

from .models import (
    Answer,
    Chapter,
    Criterion,
    ExpertProfile,
    Question,
    Questionnaire,
    Submission,
    Newsletter,
)


class ExpertCreateForm(forms.Form):
    prenume = forms.CharField(label="Prenume", max_length=150)
    nume = forms.CharField(label="Nume", max_length=150)
    email = forms.EmailField(label="Email", max_length=254)
    telefon = forms.CharField(label="Telefon", max_length=50, required=False)
    organizatie = forms.CharField(label="Organizație / instituție", max_length=255, required=False)
    functie = forms.CharField(
        label="Funcție / poziție",
        max_length=255,
        required=False,
        widget=forms.TextInput(
            attrs={
                # Unele browsere/password-managers pot face autofill (ex: "admin") chiar și cu
                # autocomplete="off" pe <form>. Încercăm să descurajăm autofill-ul.
                "autocomplete": "off",
                "data-lpignore": "true",
                "data-1p-ignore": "true",
                "autocorrect": "off",
                "spellcheck": "false",
            }
        ),
    )
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
        label="Foi de parcurs (domenii de expertiză)",
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


class StaffCreateForm(forms.Form):
    """Creează utilizatori de tip Staff (doar vizualizare în platformă).

    Implementare:
      - user.is_staff = True (pentru acces la interfața internă a platformei)
      - user.is_superuser = False (nu este Administrator)

    Accesul la /django-admin/ este restricționat la superuser (vezi cie_platform/urls.py),
    astfel încât Staff să nu poată accesa Django Admin.
    """

    prenume = forms.CharField(label="Prenume", max_length=150)
    nume = forms.CharField(label="Nume", max_length=150)
    email = forms.EmailField(label="Email", max_length=254)

    parola = forms.CharField(
        label="Parolă (opțional – dacă o lași goală, se generează automat)",
        required=False,
        widget=forms.PasswordInput,
    )
    confirma_parola = forms.CharField(label="Confirmă parola", required=False, widget=forms.PasswordInput)

    def clean_email(self):
        email = (self.cleaned_data["email"] or "").lower().strip()
        if User.objects.filter(username=email).exists() or User.objects.filter(email=email).exists():
            raise forms.ValidationError("Există deja un utilizator cu acest email.")
        return email

    def clean(self):
        cleaned = super().clean()
        parola = (cleaned.get("parola") or "").strip()
        confirma = (cleaned.get("confirma_parola") or "").strip()
        if parola or confirma:
            if parola != confirma:
                self.add_error("confirma_parola", "Parolele nu coincid.")
        return cleaned

    def save(self) -> Tuple[User, str]:
        email = (self.cleaned_data.get("email") or "").lower().strip()
        parola = (self.cleaned_data.get("parola") or "").strip()
        if not parola:
            parola = secrets.token_urlsafe(10)

        user = User.objects.create_user(
            username=email,
            email=email,
            password=parola,
            first_name=(self.cleaned_data.get("prenume") or "").strip(),
            last_name=(self.cleaned_data.get("nume") or "").strip(),
        )
        user.is_staff = True
        user.is_superuser = False
        user.is_active = True
        user.save()

        return user, parola


class StaffUpdateForm(forms.Form):
    """Editare utilizator de tip Staff (doar admin).

    Permite modificarea datelor de contact și activarea/dezactivarea contului.
    Schimbarea parolei este explicită (opt-in), pentru a evita autofill accidental.
    """

    prenume = forms.CharField(label="Prenume", max_length=150)
    nume = forms.CharField(label="Nume", max_length=150)
    email = forms.EmailField(label="Email", max_length=254)

    este_activ = forms.BooleanField(
        label="Activ",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    schimba_parola = forms.BooleanField(
        label="Schimbă parola",
        required=False,
        help_text="Bifează doar dacă vrei să setezi o parolă nouă pentru acest utilizator.",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    parola_noua = forms.CharField(
        label="Parolă nouă",
        required=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "data-lpignore": "true",
                "data-1p-ignore": "true",
            }
        ),
    )
    confirma_parola_noua = forms.CharField(
        label="Confirmă parola nouă",
        required=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "data-lpignore": "true",
                "data-1p-ignore": "true",
            }
        ),
    )

    def __init__(self, *args, user: User, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.initial.update(
            {
                "prenume": user.first_name,
                "nume": user.last_name,
                "email": user.email or user.username,
                "este_activ": bool(user.is_active),
            }
        )

        # Bootstrap styling
        for name in ["prenume", "nume", "email", "parola_noua", "confirma_parola_noua"]:
            try:
                self.fields[name].widget.attrs.setdefault("class", "form-control")
            except Exception:
                pass

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").lower().strip()
        if not email:
            raise forms.ValidationError("Email-ul este obligatoriu.")

        # username este email în platformă; păstrăm unicitatea.
        if User.objects.exclude(pk=self.user.pk).filter(Q(username=email) | Q(email=email)).exists():
            raise forms.ValidationError("Există deja un utilizator cu acest email.")
        return email

    def clean(self):
        cleaned = super().clean()
        change = bool(cleaned.get("schimba_parola"))
        p1 = (cleaned.get("parola_noua") or "").strip()
        p2 = (cleaned.get("confirma_parola_noua") or "").strip()

        if change:
            if not p1 or not p2:
                self.add_error("parola_noua", "Completează parola nouă și confirmarea.")
            elif p1 != p2:
                self.add_error("confirma_parola_noua", "Parolele nu coincid.")
        else:
            # ignorăm complet (inclusiv autofill)
            cleaned["parola_noua"] = ""
            cleaned["confirma_parola_noua"] = ""
        return cleaned

    def save(self) -> User:
        u = self.user
        u.first_name = (self.cleaned_data.get("prenume") or "").strip()
        u.last_name = (self.cleaned_data.get("nume") or "").strip()
        email = (self.cleaned_data.get("email") or "").lower().strip()
        u.email = email
        u.username = email
        u.is_active = bool(self.cleaned_data.get("este_activ"))

        # Rol fix
        u.is_staff = True
        u.is_superuser = False

        if self.cleaned_data.get("schimba_parola"):
            u.set_password(self.cleaned_data.get("parola_noua"))

        u.save()
        return u


class ExpertUpdateForm(forms.Form):
    prenume = forms.CharField(label="Prenume", max_length=150)
    nume = forms.CharField(label="Nume", max_length=150)
    email = forms.EmailField(label="Email", max_length=254, disabled=True)
    telefon = forms.CharField(label="Telefon", max_length=50, required=False)
    organizatie = forms.CharField(label="Organizație / instituție", max_length=255, required=False)
    functie = forms.CharField(
        label="Funcție / poziție",
        max_length=255,
        required=False,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "data-lpignore": "true",
                "data-1p-ignore": "true",
                "autocorrect": "off",
                "spellcheck": "false",
            }
        ),
    )
    sumar_expertiza = forms.CharField(
        label="Sumar expertiză (scurt)",
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    # Schimbarea parolei trebuie să fie explicită (opt-in). Administratorii trebuie
    # să poată actualiza datele expertului fără să fie obligați să completeze o parolă.
    # În plus, unele password-managers pot completa automat câmpurile de tip password;
    # de aceea folosim un toggle + validare doar când e bifat.
    schimba_parola = forms.BooleanField(
        label="Schimbă parola",
        required=False,
        help_text="Bifează doar dacă vrei să setezi o parolă nouă pentru acest expert.",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    parola_noua = forms.CharField(
        label="Parolă nouă",
        required=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "data-lpignore": "true",
                "data-1p-ignore": "true",
            }
        ),
    )
    confirma_parola_noua = forms.CharField(
        label="Confirmă parola nouă",
        required=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "data-lpignore": "true",
                "data-1p-ignore": "true",
            }
        ),
    )

    capitole = forms.ModelMultipleChoiceField(
        label="Capitole (domenii de expertiză)",
        queryset=Chapter.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
    )
    criterii = forms.ModelMultipleChoiceField(
        label="Foi de parcurs (domenii de expertiză)",
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

    def clean(self):
        cleaned = super().clean()
        change = bool(cleaned.get("schimba_parola"))
        p1 = (cleaned.get("parola_noua") or "").strip()
        p2 = (cleaned.get("confirma_parola_noua") or "").strip()

        if change:
            if not p1:
                self.add_error("parola_noua", "Introdu parola nouă.")
            if p1 and p1 != p2:
                self.add_error("confirma_parola_noua", "Parolele nu coincid.")
        else:
            # Dacă nu se schimbă parola, ignorăm complet câmpurile (inclusiv autofill).
            cleaned["parola_noua"] = ""
            cleaned["confirma_parola_noua"] = ""
        return cleaned

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
        if self.cleaned_data.get("schimba_parola") and parola_noua:
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
        fields = ["titlu", "descriere", "termen_limita", "este_general", "capitole", "criterii"]
        widgets = {
            "descriere": forms.Textarea(attrs={"rows": 3}),
            "termen_limita": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "este_general": forms.CheckboxInput(),
            "capitole": forms.CheckboxSelectMultiple(),
            "criterii": forms.CheckboxSelectMultiple(),
        }
        labels = {
            "titlu": "Titlu",
            "descriere": "Descriere (opțional)",
            "termen_limita": "Termen limită",
            "este_general": "General (pentru toți experții)",
            "capitole": "Capitole alocate",
            "criterii": "Foi de parcurs alocate",
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

            # Dacă există răspunsuri, permitem corectarea textului întrebărilor (typo, clarificări),
            # dar NU permitem schimbarea numărului/ordinii întrebărilor pentru a evita inconsistențe în date.
            if instance.submisii.exists():
                existing_count = len(qs)
                for i in range(1, 21):
                    if i <= existing_count:
                        self.fields[f"intrebare_{i}"].help_text = (
                            "Poți corecta formularea/erori de tipar. "
                            "Nu modifica numărul sau ordinea întrebărilor după ce există răspunsuri."
                        )
                    else:
                        self.fields[f"intrebare_{i}"].help_text = (
                            "Nu poți adăuga întrebări noi după ce au fost începute/trimise răspunsuri."
                        )

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

        # Dacă există deja submisii (ciorne sau trimise), permitem doar modificarea textului,
        # fără a schimba numărul/ordinea întrebărilor (pentru a păstra legătura cu răspunsurile existente).
        instance = getattr(self, "instance", None)
        if instance and instance.pk and instance.submisii.exists():
            existing_qs = list(instance.intrebari.all().order_by("ord"))
            existing_count = len(existing_qs)

            # În acest caz, formularul trebuie să conțină exact aceleași întrebări (ca număr),
            # doar cu text actualizat.
            if len(intrebari) != existing_count:
                raise forms.ValidationError(
                    f"Acest chestionar are deja {existing_count} întrebări (există răspunsuri). "
                    "Poți modifica doar textul lor, fără a schimba numărul."
                )

            for i in range(1, existing_count + 1):
                if not (cleaned.get(f"intrebare_{i}") or "").strip():
                    raise forms.ValidationError(
                        "Nu poți șterge întrebări după ce există răspunsuri. "
                        "Poți doar corecta textul întrebărilor existente."
                    )

            for i in range(existing_count + 1, 21):
                if (cleaned.get(f"intrebare_{i}") or "").strip():
                    raise forms.ValidationError(
                        "Nu poți adăuga întrebări noi după ce există răspunsuri. "
                        "Creează un chestionar nou dacă ai nevoie de întrebări suplimentare."
                    )

        este_general = bool(cleaned.get("este_general"))
        capitole = cleaned.get("capitole")
        criterii = cleaned.get("criterii")
        if not este_general:
            if (not capitole) and (not criterii):
                raise forms.ValidationError(
                    "Selectează cel puțin un capitol sau criteriu, sau bifează «General (pentru toți experții)»."
                )
        return cleaned

    def save(self, commit=True, user=None):
        questionnaire: Questionnaire = super().save(commit=False)
        if user and not questionnaire.pk:
            questionnaire.creat_de = user
        if commit:
            questionnaire.save()
            self.save_m2m()

            # Dacă este «General», golim alocările pe capitole/criterii (pentru claritate)
            if getattr(questionnaire, "este_general", False):
                questionnaire.capitole.clear()
                questionnaire.criterii.clear()

            # Gestionare întrebări:
            # - dacă NU există submisii: putem recrea lista de întrebări (inclusiv ordine/număr);
            # - dacă EXISTĂ submisii (ciorne/trimise): actualizăm doar textul întrebărilor existente,
            #   fără a șterge/adauga întrebări (pentru a păstra legătura cu răspunsurile).
            if questionnaire.submisii.exists():
                existing_qs = list(questionnaire.intrebari.all().order_by("ord"))
                for idx, q in enumerate(existing_qs, start=1):
                    if idx > 20:
                        break
                    new_text = (self.cleaned_data.get(f"intrebare_{idx}") or "").strip()
                    if new_text and new_text != q.text:
                        q.text = new_text[:1000]
                        q.save(update_fields=["text"])
            else:
                questionnaire.intrebari.all().delete()
                ord_no = 1
                for i in range(1, 21):
                    q_text = (self.cleaned_data.get(f"intrebare_{i}") or "").strip()
                    if q_text:
                        Question.objects.create(questionnaire=questionnaire, ord=ord_no, text=q_text[:1000])
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
                max_length=3000,
                widget=forms.Textarea(attrs={
                    "rows": 4,
                    "maxlength": "3000",
                    "placeholder": "Răspuns (max. 3000 caractere)",
                }),
            )
            self.initial[field_name] = existing.get(q.id, "")

    def save(self) -> None:
        for q in self.questionnaire.intrebari.all():
            field_name = f"q_{q.id}"
            text = (self.cleaned_data.get(field_name) or "").strip()
            answer, _ = Answer.objects.get_or_create(submission=self.submission, question=q)
            new_text = text[:3000]
            # Nu salva dacă nu s-a schimbat textul (evită actualizări inutile ale updated_at).
            if (answer.text or "") != new_text:
                answer.text = new_text
                answer.save(update_fields=["text", "updated_at"])


class ExpertImportCSVForm(forms.Form):
    fisier = forms.FileField(
        label="Fișier CSV (UTF-8)",
        help_text="Formate acceptate: .csv. Coloane: email, prenume, nume, telefon, organizatie, functie, sumar_expertiza, capitole, foi_de_parcurs (sau criterii).",
    )


class QuestionnaireImportCSVForm(forms.Form):
    fisier = forms.FileField(
        label="Fișier CSV (UTF-8)",
        help_text=(
            "Formate acceptate: .csv. Coloane: id (opțional), titlu, descriere, termen_limita, este_general, capitole, foi_de_parcurs (sau criterii), "
            "intrebare_1 ... intrebare_20. Separator recomandat pentru liste: ;"
        ),
    )



class ExpertPreferinteForm(forms.Form):
    text_mare = forms.BooleanField(
        label="Text mare (accesibilitate)",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


class NewsletterForm(forms.ModelForm):
    class Meta:
        model = Newsletter
        fields = ["subiect", "continut"]
        widgets = {
            "subiect": forms.TextInput(attrs={"class": "form-control"}),
            "continut": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 10,
                    "placeholder": "Scrie textul newsletterului aici...",
                }
            ),
        }
        labels = {
            "subiect": "Subiect",
            "continut": "Conținut (poți include linkuri: [text](https://exemplu.md))",
        }
