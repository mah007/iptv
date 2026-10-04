"""api.<domain>: the customer REST API for apps under /api/v1 (SPEC §10; ADR-0013).

Apps sign in with bearer tokens (`apps.accounts.customer_tokens`). The portal serves the
same `customer_api` routes on app.<domain>/api/v1 with its session cookie
(config.urls_portal); each authentication class answers on its own host only.
"""

from django.urls import include, path, re_path

from apps.accounts.urls_customer import token_auth
from apps.billing.urls_customer import webhook_urlpatterns
from apps.core import errors, views

# The customer API, shared by api.<domain> (tokens) and app.<domain> (session). Each app
# keeps its customer routes in apps/<app>/urls_customer.py.
customer_api = [
    path("", include("apps.accounts.urls_customer")),
    path("", include("apps.catalog.urls_customer")),
    path("", include("apps.engagement.urls_customer")),
    path("", include("apps.search.urls_customer")),
    # Billing (B1): plans, checkout, me/subscription, me/invoices.
    path("", include("apps.billing.urls_customer")),
]

urlpatterns = [
    path("", views.service_info, name="service-info"),
    path("api/v1/health", views.public_health, name="api-health"),
    path("api/v1/auth/", include(token_auth)),
    path("api/v1/", include(customer_api)),
    # Payment provider webhooks (B1, ADR-0012): api.<domain> only.
    path("api/v1/", include(webhook_urlpatterns)),
    # Last: unknown API paths answer problem+json 404, also when DEBUG is on.
    re_path(r"^api(?:/|$)", errors.api_not_found),
]

handler400 = errors.bad_request
handler403 = errors.permission_denied
handler404 = errors.page_not_found
handler500 = errors.server_error
