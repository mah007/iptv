"""app.<domain>/api: the customer portal's same-origin API (ADR-0004; built from M11b)."""

from django.urls import path, re_path

from apps.core import errors, views

urlpatterns = [
    path("api/v1/health", views.public_health, name="portal-health"),
    # Last: unknown API paths answer problem+json 404, also when DEBUG is on.
    re_path(r"^api(?:/|$)", errors.api_not_found),
]

handler400 = errors.bad_request
handler403 = errors.permission_denied
handler404 = errors.page_not_found
handler500 = errors.server_error
