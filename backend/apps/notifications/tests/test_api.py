"""Admin notification endpoints: the delivery log, retry, templates, preview and test sends."""

from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings
from django.core import mail
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory, CustomerFactory
from apps.notifications import services
from apps.notifications.defaults import EVENTS
from apps.notifications.models import NotificationOutbox, NotificationTemplate, OutboxStatus

pytestmark = pytest.mark.django_db
type Capture = Callable[..., Any]

ADMIN = {"host": settings.ADMIN_HOST}
BASE = "/api/v1/admin"


def client_for(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def test_permissions(make_admin: AdminFactory, customer_user: User) -> None:
    assert APIClient().get(f"{BASE}/notifications", headers=ADMIN).status_code == 401
    assert client_for(customer_user).get(f"{BASE}/templates", headers=ADMIN).status_code == 403
    support = client_for(make_admin("support"))
    assert support.get(f"{BASE}/notifications", headers=ADMIN).status_code == 200
    assert support.get(f"{BASE}/templates", headers=ADMIN).status_code == 200
    put = support.put(
        f"{BASE}/templates/expired/email/en", {"subject": "x", "body_text": "y"}, headers=ADMIN
    )
    assert put.status_code == 403
    assert (
        client_for(make_admin("content_manager"))
        .get(f"{BASE}/notifications", headers=ADMIN)
        .status_code
        == 403
    )


def test_the_log_with_filters_and_queries(
    owner_client: APIClient, make_customer: CustomerFactory, django_assert_num_queries: Capture
) -> None:
    for _ in range(3):
        services.enqueue(make_customer(), "expired", {"plan_name": "Basic"})
    first = NotificationOutbox.objects.first()
    assert first is not None
    services.deliver(first.pk)
    with django_assert_num_queries(3):  # roles, count, page with recipients
        page = owner_client.get(f"{BASE}/notifications", headers=ADMIN).json()
    assert page["count"] == 3
    sent = owner_client.get(f"{BASE}/notifications?status=sent", headers=ADMIN).json()
    assert sent["count"] == 1
    detail = owner_client.get(f"{BASE}/notifications/{first.pk}", headers=ADMIN).json()
    assert detail["payload"] == {"plan_name": "Basic"}
    assert detail["user"]["username"] == first.user.username  # type: ignore[union-attr]


def test_retry_endpoint(owner_client: APIClient, make_customer: CustomerFactory) -> None:
    row = services.enqueue(make_customer(), "expired", {})[0]
    url = f"{BASE}/notifications/{row.pk}/retry"
    assert owner_client.post(url, headers=ADMIN).status_code == 409
    NotificationOutbox.objects.filter(pk=row.pk).update(status=OutboxStatus.FAILED)
    assert owner_client.post(url, headers=ADMIN).json()["status"] == "queued"


def test_templates_list_every_event_in_both_languages(owner_client: APIClient) -> None:
    rows = owner_client.get(f"{BASE}/templates", headers=ADMIN).json()
    assert len(rows) == len(EVENTS) * 2
    expired = next(r for r in rows if (r["key"], r["locale"]) == ("expired", "en"))
    assert expired["is_default"] is True
    assert expired["variables"][:4] == ["service_name", "name", "support_email", "portal_url"]
    assert "plan_name" in expired["variables"]
    reset = next(r for r in rows if r["key"] == "password_reset")
    assert reset["secret"] is True


def test_edit_validate_and_reset_a_template(owner_client: APIClient, owner: User) -> None:
    url = f"{BASE}/templates/expired/email/ar"
    bad = owner_client.put(url, {"subject": "{% load static %}"}, headers=ADMIN)
    assert bad.status_code == 400
    assert bad.json()["field_error_codes"]["subject"] == ["template_invalid"]
    saved = owner_client.put(
        url, {"subject": "انتهى {{ plan_name }}", "enabled": False}, headers=ADMIN
    ).json()
    assert saved["is_default"] is False
    assert saved["subject"] == "انتهى {{ plan_name }}"
    assert saved["body_text"]  # kept from the default
    assert saved["enabled"] is False
    row = NotificationTemplate.objects.get()
    assert row.updated_by == owner
    assert owner_client.get(url, headers=ADMIN).json()["enabled"] is False
    blank = owner_client.put(url, {"body_text": "   "}, headers=ADMIN)
    assert blank.status_code == 400
    reset = owner_client.delete(url, headers=ADMIN).json()
    assert reset["is_default"] is True
    assert not NotificationTemplate.objects.exists()
    assert AuditLog.objects.filter(action__startswith="notification_template.").count() == 2
    assert owner_client.get(f"{BASE}/templates/nope/email/ar", headers=ADMIN).status_code == 404
    assert owner_client.get(f"{BASE}/templates/expired/sms/ar", headers=ADMIN).status_code == 404


def test_preview_renders_unsaved_text(owner_client: APIClient) -> None:
    response = owner_client.post(
        f"{BASE}/templates/preview",
        {
            "key": "payment_succeeded",
            "locale": "en",
            "subject": "Paid {{ amount }}",
            "body_text": "Invoice {{ invoice_number }}",
            "body_html": "<p>{{ plan_name }}</p>",
        },
        headers=ADMIN,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "Paid 79.00 SAR"
    assert body["text"] == "Invoice INV-2026-000042"
    assert "<p>Premium</p>" in body["html"]
    unknown = owner_client.post(
        f"{BASE}/templates/preview",
        {"key": "nope", "locale": "en", "subject": "s", "body_text": "b"},
        headers=ADMIN,
    )
    assert unknown.status_code == 400


def test_send_a_test_to_myself(owner_client: APIClient, owner: User) -> None:
    owner.email = "owner@example.com"
    owner.save()
    response = owner_client.post(f"{BASE}/templates/expiring_7d/email/en/test", headers=ADMIN)
    assert response.status_code == 202
    row = NotificationOutbox.objects.get()
    assert (row.user, row.locale) == (owner, "en")
    services.deliver(row.pk)
    assert mail.outbox[0].to == ["owner@example.com"]
    secret = owner_client.post(f"{BASE}/templates/password_reset/email/ar/test", headers=ADMIN)
    assert secret.status_code == 202
    assert "token=sample" in mail.outbox[1].body
    assert AuditLog.objects.filter(action="notification.test").count() == 2


def test_a_test_needs_my_email(owner_client: APIClient, owner: User) -> None:
    owner.email = ""
    owner.save()
    response = owner_client.post(f"{BASE}/templates/expired/email/en/test", headers=ADMIN)
    assert response.status_code == 409
