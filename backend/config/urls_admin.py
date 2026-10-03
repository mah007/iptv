"""admin.<domain>/api: the admin SPA's same-origin API (ADR-0004, SPEC §8.4).

Traefik sends `admin.<domain>/api*` here and everything else on that host to the
SPA. Session cookie + CSRF authentication; the schema of exactly this URLconf
feeds the generated TypeScript client (`make api-client`).
"""

from django.urls import include, path, re_path

from apps.core import errors, views
from apps.core.schema import AdminSchemaView

# Each app keeps its admin routes in apps/<app>/urls_admin.py.
admin_api = [
    path("", include("apps.core.urls_admin")),
    path("", include("apps.audit.urls_admin")),
    path("", include("apps.accounts.urls_admin")),
    path("", include("apps.catalog.urls_admin")),
    path("", include("apps.dashboard.urls_admin")),
]

urlpatterns = [
    path("api/v1/health", views.public_health, name="admin-health"),
    path("api/v1/schema", AdminSchemaView.as_view(), name="admin-schema"),
    path("api/v1/auth/", include("apps.accounts.urls_auth")),
    path("api/v1/admin/", include(admin_api)),
    # Last: unknown API paths answer problem+json 404, also when DEBUG is on.
    re_path(r"^api(?:/|$)", errors.api_not_found),
]

handler400 = errors.bad_request
handler403 = errors.permission_denied
handler404 = errors.page_not_found
handler500 = errors.server_error
