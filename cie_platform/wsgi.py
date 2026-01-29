"""WSGI config for cie_platform project."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cie_platform.settings")

application = get_wsgi_application()
