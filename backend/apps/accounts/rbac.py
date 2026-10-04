"""Role-based access control for the admin API (SPEC §6 accounts, §8.4).

The permission catalogue and the default grants of the seed roles live here. A
data migration and `manage.py seed_demo` write them with `sync_rbac`, which is
idempotent: it adds what is missing and never removes grants an owner changed.
The `owner` role always holds every permission, whatever its rows say.
"""

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

from django.db import transaction

from apps.accounts.models import Permission, Role, User, UserStatus

PERMISSIONS: Mapping[str, str] = MappingProxyType(
    {
        "dashboard.view": "See the dashboard and its KPIs.",
        "customers.view": "See customers, their access profiles and devices.",
        "customers.edit": "Create and change customers and their access profiles.",
        "devices.manage": "Add, approve, block and revoke devices; reset credentials.",
        "sessions.kill": "Stop a customer's playback sessions.",
        "subscriptions.view": "See subscriptions.",
        "subscriptions.edit": "Create, extend and change subscriptions.",
        "plans.view": "See plans.",
        "plans.edit": "Create and change plans.",
        "billing.refund": "Refund payments.",
        # Billing and notifications (B1, ADR-0012).
        "billing.view": "See payments, invoices and billing figures.",
        "billing.manage": "Record bank transfer and cash payments.",
        "notifications.view": "See the notification log and templates.",
        "notifications.manage": "Edit notification templates; resend and test messages.",
        "library.view": "See libraries, scans, titles, categories and the review queue.",
        "library.manage": "Create and change libraries, run scans, edit titles and categories.",
        "library.review": "Resolve or skip metadata matches in the review queue.",
        "settings.view": "See settings.",
        "settings.edit": "Change settings.",
        "audit.view": "See the audit log.",
        "roles.manage": "Create and change roles.",
        "admins.manage": "Create admin users and assign their roles.",
    }
)
ALL_PERMISSIONS: frozenset[str] = frozenset(PERMISSIONS)

OWNER_ROLE = "owner"

_VIEW = frozenset(code for code in PERMISSIONS if code.endswith(".view"))

# name -> (description, default grants). Owner's grants are implicit: every permission.
ROLE_DEFAULTS: Mapping[str, tuple[str, frozenset[str]]] = MappingProxyType(
    {
        OWNER_ROLE: ("Full access, including admins and roles.", ALL_PERMISSIONS),
        "admin": (
            "Runs the service day to day; cannot manage admins or roles.",
            ALL_PERMISSIONS - {"roles.manage", "admins.manage"},
        ),
        "support": (
            "Helps customers: profiles, access, devices and sessions.",
            frozenset(
                {
                    "dashboard.view",
                    "customers.view",
                    "customers.edit",
                    "devices.manage",
                    "sessions.kill",
                    "subscriptions.view",
                    "subscriptions.edit",
                    "plans.view",
                    # B1 (ADR-0012): support sees payments and messages.
                    "billing.view",
                    "notifications.view",
                }
            ),
        ),
        "content_manager": (
            "Manages the library, categories and metadata review.",
            frozenset({"dashboard.view", "library.view", "library.manage", "library.review"}),
        ),
        "viewer": ("Read-only access.", _VIEW),
    }
)
SYSTEM_ROLES: frozenset[str] = frozenset(ROLE_DEFAULTS)


def sync_permissions(permission_model: Any = Permission) -> dict[str, Any]:
    """Create missing permissions and refresh descriptions; returns them by code."""
    existing = {row.code: row for row in permission_model.objects.all()}
    for code, description in PERMISSIONS.items():
        row = existing.get(code)
        if row is None:
            existing[code] = permission_model.objects.create(code=code, description=description)
        elif row.description != description:
            row.description = description
            row.save(update_fields=["description", "updated_at"])
    return existing


def sync_rbac(permission_model: Any = Permission, role_model: Any = Role) -> None:
    """Make sure every permission and seed role exists.

    A seed role gets its default grants only when it is created, so an owner's
    later edits survive. Takes historical models when called from a migration.
    """
    with transaction.atomic():
        by_code = sync_permissions(permission_model)
        for name, (description, grants) in ROLE_DEFAULTS.items():
            role, created = role_model.objects.get_or_create(
                name=name, defaults={"description": description}
            )
            if created:
                role.permissions.set([by_code[code] for code in sorted(grants)])


def permission_codes(user: Any) -> frozenset[str]:
    """Permission codes an admin holds through roles; one query.

    Only active staff hold any. Owners (and Django superusers, who only exist
    when created from the command line) hold every permission.
    """
    if not isinstance(user, User) or not user.is_authenticated:
        return frozenset()
    if not user.is_staff or user.status != UserStatus.ACTIVE:
        return frozenset()
    if user.is_superuser:
        return ALL_PERMISSIONS
    rows = Role.objects.filter(users=user).values_list("name", "permissions__code")
    codes: set[str] = set()
    for name, code in rows:
        if name == OWNER_ROLE:
            return ALL_PERMISSIONS
        if code:
            codes.add(code)
    return frozenset(codes)


def unknown_codes(codes: Iterable[str]) -> list[str]:
    return sorted(set(codes) - ALL_PERMISSIONS)
