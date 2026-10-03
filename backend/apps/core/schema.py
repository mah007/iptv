"""OpenAPI generation (drf-spectacular) for the admin API (SPEC §8.4, §10).

- `SessionAuthenticationScheme` documents our session authentication class.
- `add_problem_details` adds the RFC 9457 `Problem` schema and the `ErrorCode`
  enum, and declares `Problem` as every operation's default (error) response, so
  the generated client knows the error shape and the full list of codes.
- `AdminSchemaView` serves the admin URLconf's schema to staff (anyone in DEBUG).
"""

from typing import Any

from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.views import SpectacularAPIView
from rest_framework.permissions import AllowAny, BasePermission, IsAdminUser

from apps.core.errors import PROBLEM_CONTENT_TYPE, ErrorCode

_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


class SessionAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.core.authentication.SessionAuthentication"
    name = "sessionAuth"

    def get_security_definition(self, auto_schema: AutoSchema) -> dict[str, str]:
        return {"type": "apiKey", "in": "cookie", "name": settings.SESSION_COOKIE_NAME}


def problem_components() -> dict[str, Any]:
    return {
        "ErrorCode": {
            "type": "string",
            "enum": [code.value for code in ErrorCode],
            "description": "Stable machine-readable error code.",
        },
        "Problem": {
            "type": "object",
            "description": "RFC 9457 problem details, served as application/problem+json.",
            "properties": {
                "type": {"type": "string", "description": "urn:smart-iptv:problem:<code>"},
                "title": {"type": "string"},
                "status": {"type": "integer"},
                "code": {"$ref": "#/components/schemas/ErrorCode"},
                "detail": {"type": "string"},
                "field_errors": {
                    "type": "object",
                    "description": "Messages per field; nested fields use dotted paths.",
                    "additionalProperties": {"type": "array", "items": {"type": "string"}},
                },
            },
            "required": ["type", "title", "status", "code", "detail"],
        },
    }


def add_problem_details(
    result: dict[str, Any], generator: object, request: object, public: bool
) -> dict[str, Any]:
    """drf-spectacular postprocessing hook (SPECTACULAR_SETTINGS["POSTPROCESSING_HOOKS"])."""
    schemas = result.setdefault("components", {}).setdefault("schemas", {})
    schemas.update(problem_components())
    default_response = {
        "description": "Error (RFC 9457 problem details).",
        "content": {PROBLEM_CONTENT_TYPE: {"schema": {"$ref": "#/components/schemas/Problem"}}},
    }
    for path_item in result.get("paths", {}).values():
        for method, operation in path_item.items():
            if method in _HTTP_METHODS:
                operation.setdefault("responses", {}).setdefault("default", default_response)
    return result


class AdminSchemaView(SpectacularAPIView):
    """GET /api/v1/schema on the admin host: staff only, open in DEBUG for local tooling."""

    urlconf = "config.urls_admin"

    def get_permissions(self) -> list[BasePermission]:
        if settings.DEBUG:
            return [AllowAny()]
        return [IsAdminUser()]
