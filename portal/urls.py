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
    path("expert/chestionar/<int:pk>/", views.expert_questionnaire, name="expert_chestionar"),

    # Admin
    path("administrare/", views.admin_dashboard, name="admin_dashboard"),

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

    path("administrare/referinte/", views.admin_referinte, name="admin_referinte"),
    path("administrare/capitole/<int:pk>/", views.admin_capitol_dashboard, name="admin_capitol_dashboard"),
    path("administrare/criterii/<int:pk>/", views.admin_criteriu_dashboard, name="admin_criteriu_dashboard"),

    path("administrare/arhiva/", views.admin_arhiva, name="admin_arhiva"),

    path("administrare/export/", views.admin_export, name="admin_export"),
]
