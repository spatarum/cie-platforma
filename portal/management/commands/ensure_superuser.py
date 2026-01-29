from __future__ import annotations

import os

from django.contrib.auth import get_user_model

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Creează automat un cont de administrator (superuser) din variabile de mediu.

    Motiv: pe planul gratuit Render nu există Shell/SSH, deci nu poți rula interactiv
    `python manage.py createsuperuser`. În schimb, setăm credențiale prin env vars și
    această comandă creează (o singură dată) superuser-ul la deploy.

    Variabile suportate (preferate):
      - CIE_ADMIN_EMAIL
      - CIE_ADMIN_PASSWORD
      - CIE_ADMIN_USERNAME (opțional; implicit email)
      - CIE_ADMIN_FIRST_NAME (opțional)
      - CIE_ADMIN_LAST_NAME (opțional)

    Compatibil și cu convențiile Django:
      - DJANGO_SUPERUSER_EMAIL
      - DJANGO_SUPERUSER_PASSWORD
      - DJANGO_SUPERUSER_USERNAME
    """

    help = "Creează automat un superuser (admin) din variabile de mediu, dacă nu există deja."

    def handle(self, *args, **options):
        email = os.environ.get("CIE_ADMIN_EMAIL") or os.environ.get("DJANGO_SUPERUSER_EMAIL")
        password = os.environ.get("CIE_ADMIN_PASSWORD") or os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        username = (
            os.environ.get("CIE_ADMIN_USERNAME")
            or os.environ.get("DJANGO_SUPERUSER_USERNAME")
            or (email or "")
        )
        first_name = os.environ.get("CIE_ADMIN_FIRST_NAME", "")
        last_name = os.environ.get("CIE_ADMIN_LAST_NAME", "")

        # Dacă nu avem suficiente date, nu e o eroare; doar sărim peste.
        if not email or not password:
            self.stdout.write(
                "ensure_superuser: lipsesc CIE_ADMIN_EMAIL și/sau CIE_ADMIN_PASSWORD. Sar peste."
            )
            return

        User = get_user_model()

        # Dacă există deja un superuser, nu facem nimic.
        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write("ensure_superuser: există deja un superuser. Sar peste.")
            return

        # Încercăm să reutilizăm un utilizator existent (după username sau email)
        user = None
        if username:
            user = User.objects.filter(username=username).first()
        if not user and email:
            user = User.objects.filter(email=email).first()

        if user:
            user.username = username or user.username
            user.email = email
            if first_name:
                user.first_name = first_name
            if last_name:
                user.last_name = last_name
            user.is_staff = True
            user.is_superuser = True
            user.set_password(password)
            user.save()
            self.stdout.write(
                self.style.SUCCESS(
                    f"ensure_superuser: utilizatorul existent '{user.username}' a fost promovat ca administrator."
                )
            )
            return

        # Creează superuser nou
        created = User.objects.create_superuser(username=username, email=email, password=password)
        if first_name:
            created.first_name = first_name
        if last_name:
            created.last_name = last_name
        created.save(update_fields=["first_name", "last_name"])

        self.stdout.write(
            self.style.SUCCESS(
                f"ensure_superuser: administrator creat: '{created.username}' ({created.email})."
            )
        )
