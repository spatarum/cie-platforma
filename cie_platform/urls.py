from django.contrib import admin
from django.urls import path, include


# Restricționăm Django Admin la superuser.
#
# În platformă, utilizatorii de tip "Staff" au user.is_staff=True pentru a putea accesa
# interfața internă (dashboard, chestionare, export etc.), dar nu trebuie să aibă acces
# la /django-admin/.
def _admin_superuser_only(request):
    return bool(request.user.is_active and request.user.is_superuser)


admin.site.has_permission = _admin_superuser_only

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("portal.urls")),
]
