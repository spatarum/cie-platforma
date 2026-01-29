from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class Cluster(models.Model):
    cod = models.PositiveSmallIntegerField(unique=True)
    denumire = models.CharField(max_length=200)
    descriere = models.TextField(blank=True)
    pictograma = models.CharField(
        max_length=100,
        blank=True,
        help_text="Clasa Bootstrap Icons, de ex. 'bi-shield-check'.",
    )
    ordonare = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "Cluster"
        verbose_name_plural = "Clustere"
        ordering = ["ordonare", "cod"]

    def __str__(self) -> str:
        return f"{self.cod}. {self.denumire}"


class Chapter(models.Model):
    numar = models.PositiveSmallIntegerField(unique=True)
    denumire = models.CharField(max_length=255)
    cluster = models.ForeignKey(
        Cluster,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="capitole",
    )
    pictograma = models.CharField(
        max_length=100,
        blank=True,
        help_text="Clasa Bootstrap Icons, de ex. 'bi-journal-text'.",
    )

    class Meta:
        verbose_name = "Capitol"
        verbose_name_plural = "Capitole"
        ordering = ["numar"]

    def __str__(self) -> str:
        return f"Cap. {self.numar} – {self.denumire}"


class Criterion(models.Model):
    cod = models.CharField(max_length=10, unique=True)
    denumire = models.CharField(max_length=255)
    pictograma = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name = "Criteriu"
        verbose_name_plural = "Criterii"
        ordering = ["cod"]

    def __str__(self) -> str:
        return self.denumire


class ExpertProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profil_expert")

    telefon = models.CharField(max_length=50, blank=True)
    organizatie = models.CharField(max_length=255, blank=True)
    functie = models.CharField(max_length=255, blank=True)
    sumar_expertiza = models.CharField(max_length=500, blank=True)

    capitole = models.ManyToManyField(Chapter, blank=True, related_name="experti")
    criterii = models.ManyToManyField(Criterion, blank=True, related_name="experti")

    class Meta:
        verbose_name = "Profil expert"
        verbose_name_plural = "Profiluri experți"

    def __str__(self) -> str:
        return f"Profil: {self.user.get_full_name() or self.user.username}"


class Questionnaire(models.Model):
    titlu = models.CharField(max_length=255)
    descriere = models.TextField(blank=True)
    termen_limita = models.DateTimeField(help_text="După termen, răspunsurile nu mai pot fi editate.")

    capitole = models.ManyToManyField(Chapter, blank=True, related_name="chestionare")
    criterii = models.ManyToManyField(Criterion, blank=True, related_name="chestionare")

    creat_de = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    creat_la = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Chestionar"
        verbose_name_plural = "Chestionare"
        ordering = ["-termen_limita", "-creat_la"]

    def __str__(self) -> str:
        return self.titlu

    @property
    def este_deschis(self) -> bool:
        return timezone.now() <= self.termen_limita


class Question(models.Model):
    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.CASCADE, related_name="intrebari")
    ord = models.PositiveSmallIntegerField()
    text = models.CharField(max_length=1000)

    class Meta:
        verbose_name = "Întrebare"
        verbose_name_plural = "Întrebări"
        ordering = ["ord"]
        unique_together = ("questionnaire", "ord")

    def __str__(self) -> str:
        return f"Î{self.ord}. {self.text[:60]}"


class Submission(models.Model):
    STATUS_DRAFT = "DRAFT"
    STATUS_TRIMIS = "TRIMIS"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Ciornă"),
        (STATUS_TRIMIS, "Trimis"),
    ]

    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.CASCADE, related_name="submisii")
    expert = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="submisii")

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    creat_la = models.DateTimeField(auto_now_add=True)
    actualizat_la = models.DateTimeField(auto_now=True)
    trimis_la = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Răspuns (set)"
        verbose_name_plural = "Răspunsuri (seturi)"
        unique_together = ("questionnaire", "expert")
        ordering = ["-actualizat_la"]

    def __str__(self) -> str:
        return f"{self.expert} → {self.questionnaire}"

    @property
    def poate_edita(self) -> bool:
        return timezone.now() <= self.questionnaire.termen_limita


class Answer(models.Model):
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="raspunsuri")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="raspunsuri")
    # Răspunsuri tip text scurt (max. 1500 caractere)
    text = models.CharField(max_length=1500, blank=True)

    class Meta:
        verbose_name = "Răspuns"
        verbose_name_plural = "Răspunsuri"
        unique_together = ("submission", "question")

    def __str__(self) -> str:
        return f"{self.submission_id}:{self.question_id}"
