from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="portal/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),

    path("", views.home, name="home"),

    # Expert
    path("expert/", views.expert_dashboard, name="expert_dashboard"),
    path("expert/profil/", views.expert_profile, name="expert_profile"),
    path("expert/contacte/", views.expert_contacte, name="expert_contacte"),
    path("expert/preferinte/", views.expert_preferinte, name="expert_preferinte"),
    path("expert/newslettere/", views.expert_newsletters, name="expert_newsletters"),
    path("expert/newslettere/<int:pk>/", views.expert_newsletter_detail, name="expert_newsletter_detail"),
    path("expert/chestionar/<int:pk>/", views.expert_questionnaire, name="expert_chestionar"),

    # Admin
    path("administrare/", views.admin_dashboard, name="admin_dashboard"),

    path("administrare/newslettere/", views.admin_newsletter_list, name="admin_newsletters_list"),
    path("administrare/newslettere/nou/", views.admin_newsletter_create, name="admin_newsletter_create"),
    path("administrare/newslettere/<int:pk>/editare/", views.admin_newsletter_edit, name="admin_newsletter_edit"),
    path("administrare/newslettere/<int:pk>/trimite/", views.admin_newsletter_send, name="admin_newsletter_send"),

    path("administrare/chestionare/", views.admin_questionnaire_list, name="admin_chestionare_list"),
    path("administrare/chestionare/nou/", views.admin_questionnaire_create, name="admin_chestionar_create"),
    path("administrare/chestionare/<int:pk>/editare/", views.admin_questionnaire_edit, name="admin_chestionar_edit"),
    path("administrare/chestionare/<int:pk>/arhivare/", views.admin_chestionar_arhivare, name="admin_chestionar_arhivare"),
    path("administrare/chestionare/<int:pk>/restabilire/", views.admin_chestionar_restabilire, name="admin_chestionar_restabilire"),
    path("administrare/chestionare/<int:pk>/raspunsuri/", views.admin_chestionar_raspunsuri, name="admin_chestionar_raspunsuri"),
    path(
        "administrare/chestionare/<int:pk>/raspunsuri/expert/<int:expert_id>/",
        views.admin_chestionar_raspunsuri_expert,
        name="admin_chestionar_raspunsuri_expert",
    ),

    path("administrare/experti/", views.admin_expert_list, name="admin_experti_list"),
    path("administrare/experti/nou/", views.admin_expert_create, name="admin_expert_create"),
    path("administrare/experti/<int:pk>/editare/", views.admin_expert_edit, name="admin_expert_edit"),
    path("administrare/experti/<int:pk>/arhivare/", views.admin_expert_arhivare, name="admin_expert_arhivare"),
    path("administrare/experti/<int:pk>/restabilire/", views.admin_expert_restabilire, name="admin_expert_restabilire"),

    # Import experți (CSV)
    path("administrare/import/experti/", views.admin_expert_import, name="admin_expert_import"),
    path("administrare/import/experti/template/", views.admin_expert_import_template, name="admin_expert_import_template"),

    # Import chestionare (CSV)
    path("administrare/import/chestionare/", views.admin_questionnaire_import, name="admin_questionnaire_import"),
    path(
        "administrare/import/chestionare/template/",
        views.admin_questionnaire_import_template,
        name="admin_questionnaire_import_template",
    ),

    path("administrare/import/rulari/<int:pk>/", views.admin_import_run_detail, name="admin_import_run_detail"),
    path("administrare/import/rulari/<int:pk>/raport.csv", views.admin_import_run_report_csv, name="admin_import_run_report_csv"),
    path("administrare/import/rulari/<int:pk>/credentiale.csv", views.admin_import_run_credentials_csv, name="admin_import_run_credentials_csv"),

    path("administrare/referinte/", views.admin_referinte, name="admin_referinte"),
    path("administrare/general/", views.admin_general_dashboard, name="admin_general_dashboard"),
    path("administrare/capitole/<int:pk>/", views.admin_capitol_dashboard, name="admin_capitol_dashboard"),
    path("administrare/criterii/<int:pk>/", views.admin_criteriu_dashboard, name="admin_criteriu_dashboard"),

    path("administrare/arhiva/", views.admin_arhiva, name="admin_arhiva"),

    path("administrare/export/", views.admin_export, name="admin_export"),
]
