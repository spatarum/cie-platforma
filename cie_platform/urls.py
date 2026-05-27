from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static


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

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
