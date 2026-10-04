"""app.<domain>/api: the customer portal's same-origin API (ADR-0004, ADR-0013).

The routes of config.urls_api's `customer_api`, signed in with a session cookie and
CSRF instead of bearer tokens. The schema of exactly this URLconf feeds the portal's
generated TypeScript client (`make api-client`, frontend/packages/api-portal).
"""

from typing import cast

from django.conf import settings
from django.urls import include, path, re_path

from apps.accounts.urls_customer import portal_auth
from apps.core import errors, views
from config.urls_api import customer_api

urlpatterns = [
    path("api/v1/health", views.public_health, name="portal-health"),
    path("api/v1/auth/", include(portal_auth)),
    path("api/v1/", include(customer_api)),
    # Last: unknown API paths answer problem+json 404, also when DEBUG is on.
    re_path(r"^api(?:/|$)", errors.api_not_found),
]

handler400 = errors.bad_request
handler403 = errors.permission_denied
handler404 = errors.page_not_found
handler500 = errors.server_error

# `manage.py spectacular --urlconf config.urls_portal --custom-settings
# config.urls_portal.SPECTACULAR_SETTINGS` (make api-client): the portal's schema.
SPECTACULAR_SETTINGS = {
    "ENUM_NAME_OVERRIDES": {
        **cast("dict[str, str]", settings.SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]),
        "PlayingType": "apps.catalog.serializers_customer.PLAYING_TYPE_CHOICES",
        "Thumb": "apps.catalog.serializers_customer.THUMB_CHOICES",
        "SearchResultType": "apps.search.serializers.RESULT_TYPE_CHOICES",
    },
    "TITLE": "Smart IPTV Customer API",
    "DESCRIPTION": "Same-origin API of the customer portal (app.<domain>/api/v1): session "
    "cookie plus CSRF. Apps use the same routes on api.<domain>/api/v1 with bearer tokens "
    "(auth/login, auth/refresh). Errors are RFC 9457 problem details.",
}
