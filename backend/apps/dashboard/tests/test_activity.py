"""GET /api/v1/admin/dashboard/activity: recent activity, or one customer's, from the audit log."""

from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import services as accounts
from apps.accounts.models import Device, User
from apps.audit import services as audit
from apps.audit.services import AuditTarget
from apps.catalog.models import Category
from apps.conftest import AdminFactory, CustomerFactory
from apps.dashboard import activity
from apps.dashboard.tests.conftest import make_movie, record_play
from apps.library.models import Library
from apps.playback import services as playback

pytestmark = pytest.mark.django_db

ADMIN = {"host": settings.ADMIN_HOST}
URL = "/api/v1/admin/dashboard/activity"


def client_for(admin: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(admin)
    return client


def test_feed_labels_targets_and_links_customers(
    owner: User, owner_client: APIClient, make_customer: CustomerFactory, library: Library
) -> None:
    sara = make_customer(name="Sara Ahmed", devices=1)
    device = Device.objects.get(user=sara)
    accounts.block_device(device, actor=owner)
    session = record_play(sara, started=timezone.now(), minutes=None, movie=make_movie("Up"))
    playback.kill_session(session, actor=owner)
    audit.record("auth.login", actor=owner, target=owner)  # stays out of the dashboard feed
    audit.record("setting.update", actor=owner, target=AuditTarget("core.setting", "xtream.port"))
    audit.record("library.update", actor=owner, target=library)
    audit.record("category.reorder", actor=owner, target=AuditTarget("catalog.category", "vod"))
    audit.record("movie.update", actor=None, target=AuditTarget("catalog.movie", "not-a-uuid"))

    body = owner_client.get(URL, headers=ADMIN).json()
    rows = body["results"]
    assert "auth.login" not in [row["action"] for row in rows]
    by_action = {row["action"]: row for row in rows}
    kill = by_action["session.kill"]
    assert kill["target_label"] == "Up"
    assert kill["customer"] == {"id": str(sara.pk), "name": "Sara Ahmed"}
    assert kill["actor"] == {"id": str(owner.pk), "name": owner.name or owner.username}
    assert kill["after"]["end_reason"] == "kicked"
    blocked = by_action["device.block"]
    assert (blocked["target_label"], blocked["customer"]["id"]) == ("Device 1", str(sara.pk))
    assert by_action["customer.create"]["target_label"] == "Sara Ahmed"
    assert by_action["setting.update"]["target_label"] == "xtream.port"
    assert by_action["library.update"]["target_label"] == library.name
    assert by_action["category.reorder"]["target_label"] == "vod"
    assert by_action["movie.update"] | {"at": None, "id": None} == {
        "id": None,
        "at": None,
        "action": "movie.update",
        "actor": None,
        "target_type": "catalog.movie",
        "target_id": "not-a-uuid",
        "target_label": "",
        "customer": None,
        "before": None,
        "after": None,
    }
    assert [row["at"] for row in rows] == sorted((row["at"] for row in rows), reverse=True)


def test_a_customers_feed_covers_devices_rules_and_sessions(
    owner: User, owner_client: APIClient, make_customer: CustomerFactory
) -> None:
    sara = make_customer(name="Sara", devices=1)
    other = make_customer(name="Other", devices=1)
    device = Device.objects.get(user=sara)
    accounts.block_device(device, actor=owner)
    accounts.block_device(Device.objects.get(user=other), actor=owner)
    rule = accounts.create_access_rule(
        user=sara, rule_type="ip_deny", value="203.0.113.9", actor=owner
    )
    accounts.delete_access_rule(rule, actor=owner)
    accounts.create_access_rule(user=None, rule_type="country_deny", value="IL", actor=owner)
    session = record_play(sara, started=timezone.now() - timedelta(minutes=1), minutes=None)
    playback.kill_session(session, actor=owner)

    body = owner_client.get(URL, {"customer": str(sara.pk)}, headers=ADMIN).json()
    actions = sorted(row["action"] for row in body["results"])
    assert actions == sorted(
        [
            "customer.create",
            "device.create",
            "device.block",
            "access_rule.create",
            "access_rule.delete",
            "session.kill",
        ]
    )
    assert body["count"] == 6
    deleted = next(row for row in body["results"] if row["action"] == "access_rule.delete")
    assert deleted["target_label"] == ""  # the rule is gone; its snapshot remains
    assert deleted["before"]["value"] == "203.0.113.9"


def test_constant_queries_per_page(
    owner: User,
    owner_client: APIClient,
    make_customer: CustomerFactory,
    library: Library,
    django_assert_num_queries: Any,
) -> None:
    for index in range(4):
        user = make_customer(name=f"C{index}", devices=1)
        accounts.block_device(Device.objects.get(user=user), actor=owner)
        audit.record("library.update", actor=owner, target=library)
        category = Category.objects.create(kind="vod", name_en=f"Cat {index}", slug=f"cat-{index}")
        audit.record("category.update", actor=owner, target=category)
    # Permissions; count; page; users; devices; libraries; categories.
    with django_assert_num_queries(7):
        body = owner_client.get(URL, {"page_size": 50}, headers=ADMIN).json()
    assert body["count"] == 4 * 5  # customer.create, device.create and .block, two updates
    assert all(row["target_label"] for row in body["results"])


def test_changes_need_audit_view_and_customer_feeds_customers_view(
    make_admin: AdminFactory, make_customer: CustomerFactory
) -> None:
    sara = make_customer(name="Sara")
    dashboard_only = client_for(make_admin(permissions=["dashboard.view"]))
    rows = dashboard_only.get(URL, headers=ADMIN).json()["results"]
    assert rows
    assert rows[0]["before"] is None
    assert rows[0]["after"] is None
    refused = dashboard_only.get(URL, {"customer": str(sara.pk)}, headers=ADMIN)
    assert refused.status_code == 403

    support = client_for(make_admin(permissions=["customers.view"]))
    assert support.get(URL, headers=ADMIN).status_code == 403
    assert support.get(URL, {"customer": str(sara.pk)}, headers=ADMIN).status_code == 200

    auditor = client_for(make_admin(permissions=["customers.view", "audit.view"]))
    rows = auditor.get(URL, {"customer": str(sara.pk)}, headers=ADMIN).json()["results"]
    assert rows[0]["after"] is not None

    nobody = client_for(make_admin(permissions=["library.view"]))
    assert nobody.get(URL, headers=ADMIN).status_code == 403
    bad = support.get(URL, {"customer": "nope"}, headers=ADMIN)
    assert bad.status_code == 400
    assert bad.json()["field_error_codes"] == {"customer": ["invalid"]}


def test_subjects_skip_unknown_types() -> None:
    entry = audit.record("thing.happen", actor=None, target=AuditTarget("unknown.thing", "1"))
    assert activity.subjects([entry]) == {}
