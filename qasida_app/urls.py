"""
URL configuration for qasida_app project.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    # Django serves the downloaded scans itself: this app runs behind gunicorn
    # with no separate media server in front of it. Put a real web server or
    # object store in front of MEDIA_ROOT before this sees production traffic.
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    path("", include("core.urls")),
]
