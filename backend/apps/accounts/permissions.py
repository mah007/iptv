"""DRF permission for the admin API: staff users holding an RBAC permission."""

from collections.abc import Mapping

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.accounts.rbac import permission_codes

# What a view requires: one code, or several of which any one suffices.
type Requirement = str | tuple[str, ...]
type Requirements = Mapping[str, Requirement]

_CODES_ATTR = "_rbac_permission_codes"
# Safe methods without their own entry fall back to GET's requirement.
_READ_ALIASES = frozenset({"HEAD", "OPTIONS"})


def request_permission_codes(request: Request) -> frozenset[str]:
    """The user's permission codes, computed once per request."""
    django_request = request._request
    codes: frozenset[str] | None = getattr(django_request, _CODES_ATTR, None)
    if codes is None:
        codes = permission_codes(request.user)
        setattr(django_request, _CODES_ATTR, codes)
    return codes


def requirement_for(view: APIView, request: Request) -> Requirement | None:
    """The view's `required_permissions` entry for this request, by action then method."""
    requirements: Requirements | None = getattr(view, "required_permissions", None)
    if not requirements:
        return None
    action = getattr(view, "action", None)
    if isinstance(action, str) and action in requirements:
        return requirements[action]
    method = (request.method or "").upper()
    if method in requirements:
        return requirements[method]
    if method in _READ_ALIASES:
        return requirements.get("GET")
    return None


class HasPermission(BasePermission):
    """Admin endpoints: active staff whose roles grant the view's required permission.

    Views declare `required_permissions = {"GET": "customers.view", "POST":
    "customers.edit"}` (keys are HTTP methods, or action names on viewsets). A
    request the mapping does not cover is refused: access fails closed (methods the
    view does not implement get 405). Non-staff users never pass, whatever their
    roles. Anonymous requests get 401.
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = request.user
        if not (user and user.is_authenticated and user.is_staff):
            return False
        method = (request.method or "").lower()
        if not hasattr(view, method):
            # Nothing to protect: the view answers 405 Method Not Allowed itself.
            return True
        requirement = requirement_for(view, request)
        if requirement is None:
            return False
        required = (requirement,) if isinstance(requirement, str) else requirement
        codes = request_permission_codes(request)
        return any(code in codes for code in required)
