"""api.<domain>: the REST API under /api/v1 (SPEC §10)."""

from django.urls import path

from apps.core import views

urlpatterns = [
    path("", views.service_info, name="service-info"),
    path("api/v1/health", views.public_health, name="api-health"),
]
