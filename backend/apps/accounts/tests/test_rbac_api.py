"""RBAC (SPEC §6, §8.3 Security): permission catalogue, roles, admins, HasPermission."""

from collections.abc import Callable
from typing import Any, ClassVar

import pytest
from django.conf import settings
from django.test import RequestFactory
from rest_framework.request import Request
from rest_framework.test import APIClient
from rest_framework.views import APIView

from apps.accounts import rbac
from apps.accounts.models import Permission, Role, User, UserStatus
from apps.accounts.permissions import HasPermission, Requirements
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
type Capture = Callable[..., Any]


def client_for(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


# --- Seed data and permission resolution ----------------------------------------------------


def test_migration_seeds_the_catalogue_and_roles() -> None:
    assert set(Permission.objects.values_list("code", flat=True)) == rbac.ALL_PERMISSIONS
    assert set(Role.objects.values_list("name", flat=True)) == rbac.SYSTEM_ROLES
    viewer = Role.objects.get(name="viewer")
    assert all(code.endswith(".view") for code in viewer.permissions.values_list("code", flat=True))


def test_sync_rbac_is_idempotent_and_keeps_edits() -> None:
    support = Role.objects.get(name="support")
    support.permissions.remove(Permission.objects.get(code="sessions.kill"))
    Permission.objects.filter(code="audit.view").update(description="stale")
    rbac.sync_rbac()
    rbac.sync_rbac()
    assert not support.permissions.filter(code="sessions.kill").exists()
    assert Permission.objects.get(code="audit.view").description == rbac.PERMISSIONS["audit.view"]
    assert Permission.objects.count() == len(rbac.PERMISSIONS)


def test_permission_codes(make_admin: AdminFactory, customer_user: User) -> None:
    owner = make_admin("owner")
    assert rbac.permission_codes(owner) == rbac.ALL_PERMISSIONS
    support = make_admin("support", "content_manager")
    assert rbac.permission_codes(support) == (
        rbac.ROLE_DEFAULTS["support"][1] | rbac.ROLE_DEFAULTS["content_manager"][1]
    )
    superuser = User.objects.create_superuser("root", password="root-pass-1234")  # noqa: S106
    assert rbac.permission_codes(superuser) == rbac.ALL_PERMISSIONS
    assert rbac.permission_codes(make_admin()) == frozenset()
    customer_user.roles.add(Role.objects.get(name="owner"))
    assert rbac.permission_codes(customer_user) == frozenset()
    owner.status = UserStatus.SUSPENDED
    owner.save()
    assert rbac.permission_codes(owner) == frozenset()
    assert rbac.unknown_codes(["audit.view", "nope"]) == ["nope"]


class _View(APIView):
    required_permissions: ClassVar[Requirements] = {
        "GET": "audit.view",
        "POST": ("settings.edit", "audit.view"),
    }
    action: str | None = None

    def get(self, request: Request) -> None: ...

    def post(self, request: Request) -> None: ...

    def delete(self, request: Request) -> None: ...


@pytest.mark.parametrize(
    ("method", "roles", "allowed"),
    [
        ("get", ("viewer",), True),
        ("head", ("viewer",), True),
        ("options", ("viewer",), True),
        ("post", ("viewer",), True),  # any one code of a tuple suffices
        ("post", ("content_manager",), False),
        ("delete", ("owner",), False),  # not in the mapping: fails closed
        ("put", ("content_manager",), True),  # not implemented: the view answers 405
    ],
)
def test_has_permission(
    method: str, roles: tuple[str, ...], allowed: bool, make_admin: AdminFactory
) -> None:
    request = Request(getattr(RequestFactory(), method)("/"))
    request.user = make_admin(*roles)
    assert HasPermission().has_permission(request, _View()) is allowed


def test_has_permission_by_action_and_without_requirements(make_admin: AdminFactory) -> None:
    request = Request(RequestFactory().get("/"))
    request.user = make_admin("viewer")
    assert HasPermission().has_permission(request, _ActionView())
    assert not HasPermission().has_permission(request, _UnguardedView())


class _ActionView(_View):
    """A viewset-style view: requirements are keyed by action name."""

    required_permissions: ClassVar[Requirements] = {"audit": "audit.view"}
    action = "audit"


class _UnguardedView(_View):
    required_permissions: ClassVar[Requirements] = {}


@pytest.mark.parametrize(
    ("method", "url", "body", "code"),
    [
        ("get", "/api/v1/admin/settings", None, "settings.view"),
        ("patch", "/api/v1/admin/settings/xtream.port", {"value": 8080}, "settings.edit"),
        ("get", "/api/v1/admin/audit", None, "audit.view"),
        ("get", "/api/v1/admin/dashboard/kpis", None, "dashboard.view"),
    ],
)
def test_admin_endpoints_need_their_permission(
    method: str, url: str, body: dict[str, Any] | None, code: str, make_admin: AdminFactory
) -> None:
    others = sorted(rbac.ALL_PERMISSIONS - {code})
    denied = client_for(make_admin(permissions=others))
    response = getattr(denied, method)(url, body, headers=ADMIN)
    assert response.status_code == 403
    allowed = client_for(make_admin(permissions=[code]))
    assert getattr(allowed, method)(url, body, headers=ADMIN).status_code == 200


# --- Permissions and roles API ------------------------------------------------------------


def test_permission_catalogue(owner_client: APIClient, django_assert_num_queries: Capture) -> None:
    with django_assert_num_queries(2):
        response = owner_client.get("/api/v1/admin/permissions", headers=ADMIN)
    assert [row["code"] for row in response.json()] == sorted(rbac.ALL_PERMISSIONS)


def test_roles_list(
    owner_client: APIClient, make_admin: AdminFactory, django_assert_num_queries: Capture
) -> None:
    make_admin("support")
    make_admin("support")
    # Permission codes, roles with admin counts, their permissions.
    with django_assert_num_queries(3):
        response = owner_client.get("/api/v1/admin/roles", headers=ADMIN)
    roles = {row["name"]: row for row in response.json()}
    assert set(roles) == rbac.SYSTEM_ROLES
    assert roles["support"]["admin_count"] == 2
    assert roles["owner"]["permissions"] == sorted(rbac.ALL_PERMISSIONS)
    assert roles["owner"]["is_system"] is True


def test_role_lifecycle(owner_client: APIClient, owner: User, make_admin: AdminFactory) -> None:
    response = owner_client.post(
        "/api/v1/admin/roles",
        {"name": "Billing_Desk", "description": "Refunds", "permissions": ["billing.refund"]},
        headers=ADMIN,
    )
    assert response.status_code == 201, response.json()
    role = response.json()
    assert role["name"] == "billing_desk"
    assert role["permissions"] == ["billing.refund"]
    assert role["is_system"] is False
    url = f"/api/v1/admin/roles/{role['id']}"
    assert owner_client.get(url, headers=ADMIN).json()["name"] == "billing_desk"

    response = owner_client.patch(
        url, {"permissions": ["billing.refund", "audit.view"]}, headers=ADMIN
    )
    assert response.json()["permissions"] == ["audit.view", "billing.refund"]
    response = owner_client.patch(url, {"permissions": ["nope.nope"]}, headers=ADMIN)
    assert response.status_code == 400
    response = owner_client.post("/api/v1/admin/roles", {"name": "billing_desk"}, headers=ADMIN)
    assert response.status_code == 400
    response = owner_client.post("/api/v1/admin/roles", {"name": "9lives"}, headers=ADMIN)
    assert response.status_code == 400

    holder = make_admin()
    holder.roles.add(Role.objects.get(pk=role["id"]))
    assert owner_client.delete(url, headers=ADMIN).status_code == 409
    holder.roles.clear()
    assert owner_client.delete(url, headers=ADMIN).status_code == 204
    actions = AuditLog.objects.filter(actor=owner).values_list("action", flat=True)
    assert sorted(actions) == ["role.create", "role.delete", "role.update"]


def test_the_owner_role_is_fixed(owner_client: APIClient) -> None:
    owner_role = Role.objects.get(name="owner")
    url = f"/api/v1/admin/roles/{owner_role.pk}"
    assert owner_client.patch(url, {"permissions": []}, headers=ADMIN).status_code == 409
    assert owner_client.delete(url, headers=ADMIN).status_code == 409


def test_role_management_needs_roles_manage(make_admin: AdminFactory) -> None:
    client = client_for(make_admin("admin"))
    assert client.get("/api/v1/admin/roles", headers=ADMIN).status_code == 403
    client = client_for(make_admin(permissions=["admins.manage"]))
    assert client.get("/api/v1/admin/roles", headers=ADMIN).status_code == 200
    assert client.post("/api/v1/admin/roles", {"name": "x"}, headers=ADMIN).status_code == 403


# --- Admins API -----------------------------------------------------------------------------


def test_admins_list(
    owner_client: APIClient, make_admin: AdminFactory, django_assert_num_queries: Capture
) -> None:
    make_admin("support")
    make_admin("viewer", "support")
    # Permission codes, count, page, roles.
    with django_assert_num_queries(4):
        response = owner_client.get("/api/v1/admin/admins", headers=ADMIN)
    body = response.json()
    assert body["count"] == 3
    assert {tuple(role["name"] for role in row["roles"]) for row in body["results"]} >= {
        ("owner",),
        ("support",),
    }


def test_create_admin_shows_the_password_once(owner_client: APIClient, owner: User) -> None:
    support = Role.objects.get(name="support")
    response = owner_client.post(
        "/api/v1/admin/admins",
        {"username": "nora", "name": "Nora", "email": "Nora@Example.com", "role_ids": [support.pk]},
        headers=ADMIN,
    )
    assert response.status_code == 201, response.json()
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert body["admin"]["roles"] == [{"id": str(support.pk), "name": "support"}]
    assert body["admin"]["mfa_enabled"] is False
    user = User.objects.get(username="nora")
    assert user.is_staff
    assert user.email == "nora@example.com"
    assert user.check_password(body["password"])
    entry = AuditLog.objects.get(action="admin.create")
    assert entry.after is not None
    assert entry.after["roles"] == ["support"]
    assert body["password"] not in str(entry.after)


def test_only_owners_grant_the_owner_role(make_admin: AdminFactory) -> None:
    manager = client_for(make_admin(permissions=["admins.manage"]))
    owner_role = Role.objects.get(name="owner")
    response = manager.post(
        "/api/v1/admin/admins", {"username": "eve", "role_ids": [owner_role.pk]}, headers=ADMIN
    )
    assert response.status_code == 403
    assert not User.objects.filter(username="eve").exists()
    target = make_admin("support")
    response = manager.patch(
        f"/api/v1/admin/admins/{target.pk}", {"role_ids": [owner_role.pk]}, headers=ADMIN
    )
    assert response.status_code == 403


def test_update_admin_roles_and_guards(
    owner_client: APIClient, owner: User, make_admin: AdminFactory
) -> None:
    target = make_admin("support")
    viewer = Role.objects.get(name="viewer")
    url = f"/api/v1/admin/admins/{target.pk}"
    response = owner_client.patch(url, {"role_ids": [viewer.pk], "name": "Tess"}, headers=ADMIN)
    assert response.status_code == 200
    assert [role["name"] for role in response.json()["roles"]] == ["viewer"]
    assert owner_client.get(url, headers=ADMIN).json()["name"] == "Tess"
    entry = AuditLog.objects.get(action="admin.update")
    assert entry.before is not None
    assert entry.after is not None
    assert entry.before["roles"] == ["support"]
    assert entry.after["roles"] == ["viewer"]

    own = f"/api/v1/admin/admins/{owner.pk}"
    response = owner_client.patch(own, {"status": "disabled"}, headers=ADMIN)
    assert response.status_code == 409
    other_owner = make_admin("owner")
    other_client = client_for(other_owner)
    # The last active owner keeps the role.
    response = other_client.patch(own, {"role_ids": []}, headers=ADMIN)
    assert response.status_code == 200
    response = owner_client.patch(
        f"/api/v1/admin/admins/{other_owner.pk}", {"role_ids": []}, headers=ADMIN
    )
    assert response.status_code == 403  # owner lost the owner role above
    response = other_client.patch(
        f"/api/v1/admin/admins/{other_owner.pk}", {"role_ids": []}, headers=ADMIN
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "At least one active owner must remain."


def test_customers_are_not_admins(owner_client: APIClient, customer_user: User) -> None:
    response = owner_client.get(f"/api/v1/admin/admins/{customer_user.pk}", headers=ADMIN)
    assert response.status_code == 404
