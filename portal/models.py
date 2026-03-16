from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
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
    culoare = models.CharField(
        max_length=7,
        blank=True,
        default="#0b3d91",
        help_text="Cod culoare HEX, de ex. #0B3D91 (folosit în interfață).",
    )
    ordonare = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "Cluster"
        verbose_name_plural = "Clustere"
        ordering = ["ordonare", "cod"]

    def __str__(self) -> str:
        return f"{self.cod}. {self.denumire}"

    @property
    def culoare_ui(self) -> str:
        return (self.culoare or "#0b3d91").lower()


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

    culoare = models.CharField(
        max_length=7,
        blank=True,
        default="#0b3d91",
        help_text="Cod culoare HEX, de ex. #0B3D91 (folosit în interfață).",
    )


    class Meta:
        verbose_name = "Capitol"
        verbose_name_plural = "Capitole"
        ordering = ["numar"]

    def __str__(self) -> str:
        return f"Cap. {self.numar} – {self.denumire}"


    @property
    def culoare_ui(self) -> str:
        return (self.culoare or "#0b3d91").lower()


class Criterion(models.Model):
    cod = models.CharField(max_length=10, unique=True)
    denumire = models.CharField(max_length=255)
    pictograma = models.CharField(max_length=100, blank=True)

    culoare = models.CharField(
        max_length=7,
        blank=True,
        default="#0b3d91",
        help_text="Cod culoare HEX, de ex. #0B3D91 (folosit în interfață).",
    )

    class Meta:
        verbose_name = "Foaie de parcurs"
        verbose_name_plural = "Foi de parcurs"
        ordering = ["cod"]

    def __str__(self) -> str:
        return self.denumire


    @property
    def culoare_ui(self) -> str:
        return (self.culoare or "#0b3d91").lower()


class ExpertProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profil_expert")

    telefon = models.CharField(max_length=50, blank=True)
    organizatie = models.CharField(max_length=255, blank=True)
    functie = models.CharField(max_length=255, blank=True)
    sumar_expertiza = models.CharField(max_length=500, blank=True)

    # Arhivare (ștergere logică)
    arhivat = models.BooleanField(default=False)
    arhivat_la = models.DateTimeField(null=True, blank=True)

    # Statistici autentificare
    numar_logari = models.PositiveIntegerField(default=0)
    ultima_logare_la = models.DateTimeField(null=True, blank=True)


    # Preferințe UI (expert)
    pref_text_mare = models.BooleanField(default=False)


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

    # Categorie specială: chestionare pentru toți experții (General)
    este_general = models.BooleanField(
        default=False,
        help_text="Dacă este bifat, chestionarul este disponibil pentru toți experții (categoria «General»).",
    )

    capitole = models.ManyToManyField(Chapter, blank=True, related_name="chestionare")
    criterii = models.ManyToManyField(Criterion, blank=True, related_name="chestionare")

    creat_de = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    creat_la = models.DateTimeField(auto_now_add=True)

    # Arhivare (ștergere logică)
    arhivat = models.BooleanField(default=False)
    arhivat_la = models.DateTimeField(null=True, blank=True)


    class Meta:
        verbose_name = "Chestionar"
        verbose_name_plural = "Chestionare"
        ordering = ["-termen_limita", "-creat_la"]

    def __str__(self) -> str:
        return self.titlu

    @property
    def este_deschis(self) -> bool:
        return timezone.now() <= self.termen_limita


class QuestionnaireScopeSnapshot(models.Model):
    """Snapshot (înghețare) pentru rata de răspuns la închiderea unui chestionar.

    De ce există:
    - Pentru chestionarele închise, rata de răspuns NU trebuie să se modifice dacă ulterior apar
      experți noi (sau se modifică alocările).
    - Pentru paginile de capitol / foaie de parcurs (criteriu) și statistica sintetică din Panou,
      avem nevoie de un număr de experți (denominator) "înghețat" la termenul limită.

    Snapshot-urile sunt pe "scope":
      - GENERAL: pentru chestionare generale
      - CHAPTER: pentru un capitol
      - CRITERION: pentru o foaie de parcurs
    """

    SCOPE_GENERAL = "GENERAL"
    SCOPE_CHAPTER = "CHAPTER"
    SCOPE_CRITERION = "CRITERION"

    SCOPE_CHOICES = [
        (SCOPE_GENERAL, "General"),
        (SCOPE_CHAPTER, "Capitol"),
        (SCOPE_CRITERION, "Foaie de parcurs"),
    ]

    questionnaire = models.ForeignKey(
        Questionnaire,
        on_delete=models.CASCADE,
        related_name="scope_snapshots",
    )

    scope = models.CharField(max_length=20, choices=SCOPE_CHOICES, db_index=True)
    # Cheie stabilă (evită problemele de unicitate cu NULL):
    #   GENERAL
    #   CH:<chapter_id>
    #   CR:<criterion_id>
    scope_key = models.CharField(max_length=64, db_index=True)

    chapter = models.ForeignKey(
        Chapter,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="questionnaire_scope_snapshots",
    )
    criterion = models.ForeignKey(
        Criterion,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="questionnaire_scope_snapshots",
    )

    frozen_for_deadline = models.DateTimeField(
        help_text="Termenul limită al chestionarului pentru care au fost înghețate valorile.",
    )
    frozen_la = models.DateTimeField(auto_now_add=True)

    # Denominator / numerator înghețate la termen:
    nr_experti = models.PositiveIntegerField(default=0)
    nr_raspunsuri = models.PositiveIntegerField(default=0)

    # Pentru afișare comodă (badge-uri), păstrăm și ID-urile experților care au trimis.
    # (Lista poate fi goală; nu e folosită pentru calcule, ci pentru UI.)
    respondent_ids = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = "Snapshot rată răspuns"
        verbose_name_plural = "Snapshot-uri rată răspuns"
        constraints = [
            models.UniqueConstraint(
                fields=["questionnaire", "scope_key"],
                name="uniq_questionnaire_scope_snapshot",
            )
        ]

    def __str__(self) -> str:
        return f"Snapshot {self.scope_key} – Q{self.questionnaire_id}"

    @staticmethod
    def make_scope_key(scope: str, chapter_id: int | None = None, criterion_id: int | None = None) -> str:
        if scope == QuestionnaireScopeSnapshot.SCOPE_GENERAL:
            return "GENERAL"
        if scope == QuestionnaireScopeSnapshot.SCOPE_CHAPTER:
            return f"CH:{int(chapter_id)}"
        if scope == QuestionnaireScopeSnapshot.SCOPE_CRITERION:
            return f"CR:{int(criterion_id)}"
        raise ValueError("Scope invalid")

    @property
    def rata(self) -> float:
        return round((self.nr_raspunsuri / self.nr_experti) * 100, 1) if self.nr_experti else 0.0

    def save(self, *args, **kwargs):
        if not self.scope_key:
            self.scope_key = self.make_scope_key(
                self.scope,
                chapter_id=self.chapter_id,
                criterion_id=self.criterion_id,
            )
        if not self.frozen_for_deadline:
            self.frozen_for_deadline = self.questionnaire.termen_limita
        return super().save(*args, **kwargs)


class Newsletter(models.Model):
    """Newsletter trimis către toți experții."""

    subiect = models.CharField(max_length=255)
    continut = models.TextField(
        help_text=(
            "Textul newsletterului. Poți include hyperlinkuri folosind formatul: "
            "[text](https://exemplu.md)"
        )
    )
    continut_html = models.TextField(blank=True)

    creat_de = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="newsletter_create",
    )
    creat_la = models.DateTimeField(auto_now_add=True)

    trimis_la = models.DateTimeField(null=True, blank=True)
    trimis_de = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="newsletter_trimite",
    )

    nr_destinatari = models.PositiveIntegerField(default=0)
    nr_trimise = models.PositiveIntegerField(default=0)
    nr_esecuri = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Newsletter"
        verbose_name_plural = "Newslettere"
        ordering = ["-creat_la"]

    def __str__(self) -> str:
        return self.subiect

    def save(self, *args, **kwargs):
        """Păstrează `continut_html` sincronizat cu `continut`.

        `continut_html` este folosit atât pentru previzualizarea din platformă, cât și pentru corpul HTML
        al emailului. Îl generăm mereu din `continut` pentru a evita inconsecvențe (ex: editare din Django Admin).
        """
        try:
            from .textutils import newsletter_text_to_html

            self.continut_html = newsletter_text_to_html(self.continut or "")
        except Exception:
            # Fallback sigur: nu blocăm salvarea dacă apare o problemă de import/format.
            # În cel mai rău caz rămâne varianta existentă / goală.
            if self.continut_html is None:
                self.continut_html = ""
        return super().save(*args, **kwargs)

    @property
    def este_trimis(self) -> bool:
        return bool(self.trimis_la)




class ImportRun(models.Model):
    KIND_EXPERTI = "EXPERTI"
    KIND_CHESTIONARE = "CHESTIONARE"
    KIND_PNA = "PNA"

    KIND_CHOICES = [
        (KIND_EXPERTI, "Import experți"),
        (KIND_CHESTIONARE, "Import chestionare"),
        (KIND_PNA, "Import PNA"),
    ]

    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=KIND_EXPERTI)
    creat_de = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="importuri_create",
    )
    creat_la = models.DateTimeField(auto_now_add=True)
    nume_fisier = models.CharField(max_length=255, blank=True)

    nr_create = models.PositiveIntegerField(default=0)
    nr_actualizate = models.PositiveIntegerField(default=0)
    nr_erori = models.PositiveIntegerField(default=0)

    raport_csv = models.TextField(blank=True)
    cred_csv = models.TextField(blank=True)

    class Meta:
        verbose_name = "Rulare import"
        verbose_name_plural = "Rulări import"
        ordering = ["-creat_la"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} – {self.creat_la:%d.%m.%Y %H:%M}"


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
    # Răspunsuri tip text scurt (max. 3000 caractere)
    text = models.CharField(max_length=3000, blank=True)

    # Pentru workflow-ul de comentarii (staff/admin) este util să știm când s-a modificat răspunsul.
    # (Auto-update la fiecare salvare a răspunsului.)
    updated_at = models.DateTimeField(auto_now=True)

    # Status thread comentarii (per răspuns / per întrebare)
    comentarii_rezolvat = models.BooleanField(default=False)
    comentarii_rezolvat_la = models.DateTimeField(null=True, blank=True)
    comentarii_rezolvat_de = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="answer_threads_rezolvat",
    )

    class Meta:
        verbose_name = "Răspuns"
        verbose_name_plural = "Răspunsuri"
        unique_together = ("submission", "question")

    def __str__(self) -> str:
        return f"{self.submission_id}:{self.question_id}"


class AnswerComment(models.Model):
    """Comentarii (staff/admin) pe fiecare răspuns (Answer).

    Comentariile sunt vizibile și pentru experți (în pagina chestionarului),
    însă doar utilizatorii interni (Staff/Admin) pot crea / edita / șterge.
    """

    answer = models.ForeignKey(
        Answer,
        on_delete=models.CASCADE,
        related_name="comentarii",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="answer_comments",
    )
    text = models.TextField(max_length=2000)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Pentru a detecta dacă răspunsul a fost modificat după comentariu.
    answer_updated_at_snapshot = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Comentariu la răspuns"
        verbose_name_plural = "Comentarii la răspunsuri"
        ordering = ["created_at"]

    def __str__(self) -> str:
        who = "(anonim)" if not self.author_id else (self.author.get_full_name() or self.author.username)
        return f"Comentariu {self.id} de {who}"

    def save(self, *args, **kwargs):
        if self.answer_updated_at_snapshot is None and self.answer_id:
            try:
                self.answer_updated_at_snapshot = self.answer.updated_at
            except Exception:
                pass
        return super().save(*args, **kwargs)


# -------------------- PNA (Programul Național de Aderare) --------------------


class PnaInstitution(models.Model):
    """Instituție responsabilă în PNA.

    În etapa 1 este administrată doar de Administrator (fără Django Admin).
    """

    nume = models.CharField(max_length=400, unique=True)
    creat_la = models.DateTimeField(auto_now_add=True)
    actualizat_la = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Instituție PNA"
        verbose_name_plural = "Instituții PNA"
        ordering = ["nume"]

    def __str__(self) -> str:
        return self.nume

    def save(self, *args, **kwargs):
        if self.nume:
            self.nume = self.nume.strip()
        return super().save(*args, **kwargs)


class EUAct(models.Model):
    """Act UE (ex: Directivă / Regulament) care trebuie transpus/implementat."""

    celex = models.CharField(max_length=32, unique=True)
    denumire = models.CharField(max_length=700)
    tip_document = models.CharField(max_length=200, blank=True)
    url = models.URLField(blank=True)

    class Meta:
        verbose_name = "Act UE"
        verbose_name_plural = "Acte UE"
        ordering = ["celex"]

    def __str__(self) -> str:
        return f"{self.celex} – {self.denumire[:60]}" if self.denumire else self.celex

    @property
    def celex_curat(self) -> str:
        raw = (self.celex or "").strip()
        raw = raw.replace("CELEX:", "").replace("celex:", "").strip()
        return raw

    @property
    def url_final(self) -> str:
        if self.url:
            return self.url
        # Link standard către EUR-Lex. În practică, EUR-Lex acceptă URI=CELEX:<cod>
        # și redirecționează la pagina actului.
        if not self.celex_curat:
            return ""
        return f"https://eur-lex.europa.eu/legal-content/RO/TXT/?uri=CELEX:{self.celex_curat}"


class PnaProject(models.Model):
    """Unitate logică din PNA monitorizată în platformă ("proiect de lege").

    NOTĂ: aici "proiect de lege" NU înseamnă redactarea textului de lege, ci o acțiune
    normativă ce trebuie monitorizată și analizată.

    În etapa 1 implementăm doar în profilul administratorului.
    """

    COMPLEXITATE_CHOICES = [
        (1, "Foarte redusă"),
        (2, "Redusă"),
        (3, "Medie"),
        (4, "Ridicată"),
        (5, "Foarte ridicată"),
    ]
    PRIORITATE_CHOICES = [
        (1, "Scăzută"),
        (2, "Medie"),
        (3, "Înaltă"),
    ]
    EXPERTIZA_INTERNA_CHOICES = [
        (1, "Insuficientă"),
        (2, "Parțială"),
        (3, "Disponibilă"),
    ]

    STATUS_NEINCEPUT = "NEINCEPUT"
    STATUS_IN_LUCRU_GUVERN = "IN_LUCRU_GUVERN"
    STATUS_IN_AVIZARE_GUVERN = "IN_AVIZARE_GUVERN"
    STATUS_ADOPTAT_GUVERN = "ADOPTAT_GUVERN"
    STATUS_IN_AVIZARE_CE = "IN_AVIZARE_CE"
    STATUS_IN_PROCEDURA_PARLAMENT = "IN_PROCEDURA_PARLAMENT"
    STATUS_ADOPTAT_PARLAMENT = "ADOPTAT_PARLAMENT"

    STATUS_IMPLEMENTARE_CHOICES = [
        (STATUS_NEINCEPUT, "Neînceput"),
        (STATUS_IN_LUCRU_GUVERN, "În lucru la Guvern"),
        (STATUS_IN_AVIZARE_GUVERN, "În avizare la Guvern"),
        (STATUS_ADOPTAT_GUVERN, "Adoptat de Guvern"),
        (STATUS_IN_AVIZARE_CE, "În avizare la Comisia Europeană"),
        (STATUS_IN_PROCEDURA_PARLAMENT, "În procedură legislativă la Parlament"),
        (STATUS_ADOPTAT_PARLAMENT, "Adoptat de Parlament"),
    ]

    titlu = models.CharField(max_length=700)

    # Fiecare proiect este atașat fie la un capitol, fie la o foaie de parcurs.
    chapter = models.ForeignKey(
        Chapter,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pna_proiecte",
    )
    criterion = models.ForeignKey(
        Criterion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pna_proiecte",
    )

    acte_ue = models.ManyToManyField(
        EUAct,
        through="PnaProjectEUAct",
        related_name="proiecte_pna",
        blank=True,
    )

    # Elemente de bază (tabel PNA)
    # Legacy text (păstrat pentru compatibilitate / import). În UI folosim referințe la instituții.
    institutie_principala = models.CharField(max_length=300, blank=True)
    institutie_coreponsabila = models.CharField(max_length=300, blank=True)

    institutie_principala_ref = models.ForeignKey(
        PnaInstitution,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="proiecte_principale",
    )
    institutii_responsabile = models.ManyToManyField(
        PnaInstitution,
        blank=True,
        related_name="proiecte_responsabile",
    )

    termen_aprobare_guvern = models.DateField(null=True, blank=True)
    termen_aprobare_parlament = models.DateField(null=True, blank=True)
    termen_actualizat_aprobare_guvern = models.DateField(null=True, blank=True)

    status_implementare = models.CharField(
        max_length=40,
        choices=STATUS_IMPLEMENTARE_CHOICES,
        default=STATUS_NEINCEPUT,
    )

    # Detalii / meta
    descriere = models.TextField(blank=True)
    contact_responsabil = models.CharField(max_length=300, blank=True)
    contact_responsabil_email = models.EmailField(blank=True)

    # Referințe / condiționalități
    raport_extindere_2023 = models.BooleanField(default=False)
    raport_extindere_2024 = models.BooleanField(default=False)
    raport_extindere_2025 = models.BooleanField(default=False)
    raport_extindere_2026 = models.BooleanField(default=False)
    raport_extindere_2027 = models.BooleanField(default=False)

    plan_crestere_economica = models.BooleanField(default=False)
    necesita_avizare_comisia_europeana = models.BooleanField(default=False)

    # Evaluări (etapa 1: introduse manual de admin)
    complexitate = models.PositiveSmallIntegerField(null=True, blank=True, choices=COMPLEXITATE_CHOICES)
    prioritate = models.PositiveSmallIntegerField(null=True, blank=True, choices=PRIORITATE_CHOICES)
    expertiza_interna = models.PositiveSmallIntegerField(null=True, blank=True, choices=EXPERTIZA_INTERNA_CHOICES)

    volum_munca_zile = models.PositiveIntegerField(null=True, blank=True)
    necesita_expertiza_externa = models.BooleanField(default=False)
    disponibilitate_expertiza_externa = models.TextField(blank=True)
    parteneri_societate_civila = models.TextField(blank=True)

    # Costuri pe ani (lei) – etapă 1
    cost_2026 = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    cost_2027 = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    cost_2028 = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    cost_2029 = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    # Riscuri (etapa 1)
    riscuri = models.TextField(blank=True)

    # Criterii de analiză (comentarii vor fi completate ulterior de experți/staff – etapă 2)
    analiza_flexibilitate = models.TextField(blank=True)
    analiza_gestiunea_impactului = models.TextField(blank=True)
    analiza_potential_negociere = models.TextField(blank=True)

    # Elemente suplimentare utile din tabelul PNA (import)
    pna_cluster = models.CharField(max_length=300, blank=True)
    pna_prioritate_text = models.CharField(max_length=100, blank=True)
    pna_nr_actiune = models.CharField(max_length=50, blank=True)
    pna_cod_unic = models.CharField(max_length=255, blank=True)
    indicator_monitorizare = models.TextField(blank=True)
    comentariu_pna = models.TextField(blank=True)
    intarziat_2025 = models.BooleanField(default=False)
    note_explicative = models.TextField(blank=True)
    partener_de_dezvoltare = models.CharField(max_length=300, blank=True)
    executor_actiune = models.CharField(max_length=300, blank=True)

    cost_total_mii_lei = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    cost_buget_stat_mii_lei = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    cost_asistenta_externa_mii_lei = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    cost_neacoperite_mii_lei = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    acte_normative_transpunere_existente = models.TextField(blank=True)

    creat_de = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pna_proiecte_create",
    )
    creat_la = models.DateTimeField(auto_now_add=True)
    actualizat_la = models.DateTimeField(auto_now=True)

    arhivat = models.BooleanField(default=False)
    arhivat_la = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Proiect PNA"
        verbose_name_plural = "Proiecte PNA"
        ordering = ["-actualizat_la", "titlu"]

    def __str__(self) -> str:
        return self.titlu

    @property
    def atasare_label(self) -> str:
        if self.chapter_id:
            return f"Cap. {self.chapter.numar} – {self.chapter.denumire}"
        if self.criterion_id:
            return f"{self.criterion.cod} – {self.criterion.denumire}"
        return "(neatribuit)"

    @property
    def termen_guvern_efectiv(self):
        """Termenul de guvern folosit în practică (actualizat dacă există)."""
        return self.termen_actualizat_aprobare_guvern or self.termen_aprobare_guvern

    @property
    def termen_deadline(self):
        """Deadline-ul folosit în dashboard/matrice.

        Regulă cerută:
        - dacă există termen actualizat (Guvern) → se folosește acesta
        - altfel → termenul de Parlament (din PNA)
        - fallback: termenul inițial de Guvern
        """
        return (
            self.termen_actualizat_aprobare_guvern
            or self.termen_aprobare_parlament
            or self.termen_aprobare_guvern
        )

    def clean(self):
        # exact un scope
        has_ch = bool(self.chapter_id)
        has_cr = bool(self.criterion_id)
        if has_ch and has_cr:
            raise ValidationError("Proiectul trebuie atașat fie la un capitol, fie la o foaie de parcurs (nu ambele).")
        if not has_ch and not has_cr:
            raise ValidationError("Proiectul trebuie atașat la un capitol sau la o foaie de parcurs.")


class PnaProjectEUAct(models.Model):
    """Legătura proiect PNA ↔ act UE, cu informații suplimentare per act."""

    TIP_TRANSPUNERE_TOTAL = "TOTAL"
    TIP_TRANSPUNERE_PARTIAL = "PARTIAL"
    TIP_TRANSPUNERE_CHOICES = [
        (TIP_TRANSPUNERE_TOTAL, "Transpus total"),
        (TIP_TRANSPUNERE_PARTIAL, "Transpus parțial"),
    ]

    project = models.ForeignKey(PnaProject, on_delete=models.CASCADE, related_name="acte_ue_legaturi")
    eu_act = models.ForeignKey(EUAct, on_delete=models.CASCADE, related_name="pna_legaturi")
    tip_transpunere = models.CharField(max_length=20, blank=True, choices=TIP_TRANSPUNERE_CHOICES)

    class Meta:
        verbose_name = "Act UE în proiect"
        verbose_name_plural = "Acte UE în proiecte"
        unique_together = ("project", "eu_act")

    def __str__(self) -> str:
        return f"{self.project_id} ↔ {self.eu_act_id}"
