from django.contrib import admin

from .models import (
    Answer,
    Chapter,
    Cluster,
    Criterion,
    ExpertProfile,
    Question,
    Questionnaire,
    Submission,
    Newsletter,
)


@admin.register(Cluster)
class ClusterAdmin(admin.ModelAdmin):
    list_display = ("cod", "denumire", "ordonare")
    list_editable = ("ordonare",)
    search_fields = ("denumire",)


@admin.register(Chapter)
class ChapterAdmin(admin.ModelAdmin):
    list_display = ("numar", "denumire", "cluster", "pictograma", "culoare")
    list_filter = ("cluster",)
    search_fields = ("denumire",)


@admin.register(Criterion)
class CriterionAdmin(admin.ModelAdmin):
    list_display = ("cod", "denumire", "pictograma", "culoare")
    search_fields = ("denumire",)


@admin.register(ExpertProfile)
class ExpertProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "organizatie", "functie")
    search_fields = ("user__first_name", "user__last_name", "user__email", "organizatie", "functie")
    filter_horizontal = ("capitole", "criterii")


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 1
    max_num = 20


@admin.register(Questionnaire)
class QuestionnaireAdmin(admin.ModelAdmin):
    list_display = ("titlu", "termen_limita", "creat_la")
    list_filter = ("termen_limita", "capitole", "criterii")
    search_fields = ("titlu",)
    filter_horizontal = ("capitole", "criterii")
    inlines = [QuestionInline]


class AnswerInline(admin.TabularInline):
    model = Answer
    extra = 0


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("questionnaire", "expert", "status", "actualizat_la", "trimis_la")
    list_filter = ("status", "questionnaire")
    search_fields = ("expert__first_name", "expert__last_name", "expert__email", "questionnaire__titlu")
    inlines = [AnswerInline]


admin.site.register(Answer)
admin.site.register(Question)


@admin.register(Newsletter)
class NewsletterAdmin(admin.ModelAdmin):
    list_display = ("subiect", "creat_la", "trimis_la", "nr_trimise", "nr_esecuri")
    search_fields = ("subiect", "continut")
