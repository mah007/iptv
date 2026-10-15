"""api.<domain>: the customer REST API for apps under /api/v1 (SPEC §10; ADR-0013).

Apps sign in with bearer tokens (`apps.accounts.customer_tokens`). The portal serves the
same `customer_api` routes on app.<domain>/api/v1 with its session cookie
(config.urls_portal); each authentication class answers on its own host only.
"""

from django.urls import URLPattern, URLResolver, include, path, re_path

from apps.accounts.urls_customer import token_auth
from apps.billing.urls_customer import webhook_urlpatterns
from apps.core import errors, views
from apps.core.api import BrandingView, LegalPageView

# The customer API, shared by api.<domain> (tokens) and app.<domain> (session). Each app
# keeps its customer routes in apps/<app>/urls_customer.py.
customer_api: list[URLPattern | URLResolver] = [
    path("", include("apps.accounts.urls_customer")),
    path("", include("apps.catalog.urls_customer")),
    path("", include("apps.engagement.urls_customer")),
    path("", include("apps.search.urls_customer")),
    # Live TV (Q1, ADR-0023): channels, now and next, guides; playback via playback/start.
    path("", include("apps.live.urls_customer")),
    # Billing (B1): plans, checkout, me/subscription, me/invoices.
    path("", include("apps.billing.urls_customer")),
    # Public branding: name, logos, colour, support contacts (CFG, ADR-0019).
    path("branding", BrandingView.as_view(), name="branding"),
    # Public legal pages: privacy policy and terms (Q1, ADR-0023).
    re_path(r"^legal/(?P<document>privacy|terms)$", LegalPageView.as_view(), name="legal"),
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
