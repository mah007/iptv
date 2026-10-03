"""api.<domain>: the REST API under /api/v1 for external and token clients (SPEC §10)."""

from django.urls import path, re_path

from apps.core import errors, views

urlpatterns = [
    path("", views.service_info, name="service-info"),
    path("api/v1/health", views.public_health, name="api-health"),
    # Last: unknown API paths answer problem+json 404, also when DEBUG is on.
    re_path(r"^api(?:/|$)", errors.api_not_found),
]

handler400 = errors.bad_request
handler403 = errors.permission_denied
handler404 = errors.page_not_found
handler500 = errors.server_error
