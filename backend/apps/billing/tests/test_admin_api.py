"""Admin billing endpoints: plans, subscriptions, payments, invoices and KPIs (RBAC,
audit, query counts)."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.billing import kpis, services, subscriptions
from apps.billing.models import Payment, Plan, Subscription, SubscriptionStatus
from apps.billing.tests.conftest import PlanFactory
from apps.catalog.models import Category
from apps.conftest import AdminFactory, CustomerFactory

pytestmark = pytest.mark.django_db
type Capture = Callable[..., Any]

ADMIN = {"host": settings.ADMIN_HOST}
BASE = "/api/v1/admin"


def client_for(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer(devices=1)


# --- Permissions ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/plans"),
        ("get", "/subscriptions"),
        ("get", "/payments"),
        ("get", "/invoices"),
        ("get", "/dashboard/billing"),
    ],
)
def test_anonymous_and_roleless_are_refused(
    make_admin: AdminFactory, customer_user: User, method: str, path: str
) -> None:
    assert getattr(APIClient(), method)(BASE + path, headers=ADMIN).status_code == 401
    assert getattr(client_for(customer_user), method)(BASE + path, headers=ADMIN).status_code == 403
    assert getattr(client_for(make_admin()), method)(BASE + path, headers=ADMIN).status_code == 403


def test_role_grants(make_admin: AdminFactory, plan: Plan, customer: User) -> None:
    support = client_for(make_admin("support"))
    assert support.get(f"{BASE}/plans", headers=ADMIN).status_code == 200
    assert support.post(f"{BASE}/plans", {}, headers=ADMIN).status_code == 403
    assert support.get(f"{BASE}/payments", headers=ADMIN).status_code == 200
    body = {"user_id": str(customer.pk), "plan_id": str(plan.pk)}
    assert support.post(f"{BASE}/payments", body, headers=ADMIN).status_code == 403
    assert support.post(f"{BASE}/subscriptions", body, headers=ADMIN).status_code == 201
    viewer = client_for(make_admin("viewer"))
    assert viewer.get(f"{BASE}/dashboard/billing", headers=ADMIN).status_code == 200
    payment = services.record_manual_payment(user=customer, plan=plan, actor=None)
    refund = f"{BASE}/payments/{payment.pk}/refund"
    assert client_for(make_admin("admin")).post(refund, {}, headers=ADMIN).status_code == 200
    assert support.post(refund, {}, headers=ADMIN).status_code == 403


# --- Plans --------------------------------------------------------------------------------------


def test_plan_crud(owner_client: APIClient, category: Category, owner: User) -> None:
    response = owner_client.post(
        f"{BASE}/plans",
        {
            "code": "premium",
            "name_en": "Premium",
            "name_ar": "المميزة",
            "price": 7900,
            "duration_months": 1,
            "duration_days": 0,
            "max_streams": 4,
            "max_quality": 2160,
            "category_ids": [str(category.pk)],
        },
        headers=ADMIN,
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["category_ids"] == [str(category.pk)]
    assert body["price_total"]["total"] == 7900
    assert body["price_total"]["display_ar"] == "79.00 ر.س"
    assert body["subscribers"] == 0
    plan_url = f"{BASE}/plans/{body['id']}"
    assert owner_client.get(plan_url, headers=ADMIN).json()["code"] == "premium"
    patched = owner_client.patch(plan_url, {"price": 8900}, headers=ADMIN).json()
    assert (patched["price"], patched["version"]) == (8900, 2)
    assert owner_client.delete(plan_url, headers=ADMIN).status_code == 204
    assert AuditLog.objects.filter(action__startswith="plan.").count() == 3


def test_plan_validation_errors(owner_client: APIClient, plan: Plan) -> None:
    response = owner_client.post(
        f"{BASE}/plans",
        {"code": "standard", "name_en": "X", "name_ar": "س", "price": 100},
        headers=ADMIN,
    )
    assert response.status_code == 400
    assert response.json()["field_error_codes"]["code"] == ["code_taken"]
    response = owner_client.post(
        f"{BASE}/plans",
        {"code": "bad", "name_en": "X", "name_ar": "س", "price": -1, "currency": "sar"},
        headers=ADMIN,
    )
    assert set(response.json()["field_errors"]) == {"price", "currency"}


def test_plan_list_order_reorder_and_queries(
    owner_client: APIClient,
    make_plan: PlanFactory,
    customer: User,
    django_assert_num_queries: Capture,
) -> None:
    plans = [make_plan(sort=index) for index in range(3)]
    subscriptions.activate(customer, plans[1], source="manual")
    owner_client.get(f"{BASE}/plans", headers=ADMIN)  # warm the settings cache
    with django_assert_num_queries(3):  # roles, plans with counts, their categories
        rows = owner_client.get(f"{BASE}/plans", headers=ADMIN).json()
    assert [row["id"] for row in rows] == [str(plan.pk) for plan in plans]
    assert rows[1]["subscribers"] == 1
    reordered = owner_client.post(
        f"{BASE}/plans/reorder",
        {"ids": [str(plans[2].pk), str(plans[0].pk), str(plans[1].pk)]},
        headers=ADMIN,
    ).json()
    assert [row["id"] for row in reordered] == [str(plans[i].pk) for i in (2, 0, 1)]
    active = owner_client.get(f"{BASE}/plans?active=true", headers=ADMIN).json()
    assert len(active) == 3


def test_deleting_a_used_plan_is_a_conflict(
    owner_client: APIClient, plan: Plan, customer: User
) -> None:
    subscriptions.activate(customer, plan, source="manual")
    assert owner_client.delete(f"{BASE}/plans/{plan.pk}", headers=ADMIN).status_code == 409


def test_migrate_subscriptions(owner_client: APIClient, plan: Plan, customer: User) -> None:
    subscriptions.activate(customer, plan, source="manual")
    response = owner_client.post(f"{BASE}/plans/{plan.pk}/migrate-subscriptions", headers=ADMIN)
    assert response.json() == {"migrated": 1}


# --- Subscriptions --------------------------------------------------------------------------------


def test_create_through_activate_and_act_on_it(
    owner_client: APIClient, plan: Plan, make_plan: PlanFactory, customer: User
) -> None:
    response = owner_client.post(
        f"{BASE}/subscriptions",
        {"user_id": str(customer.pk), "plan_id": str(plan.pk), "note": "Paid at the shop"},
        headers=ADMIN,
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["status"] == "active"
    assert body["user"]["username"] == customer.username
    assert body["plan"]["code"] == "standard"
    assert body["grace_until"] is not None
    assert body["created_by"] == "owner"
    url = f"{BASE}/subscriptions/{body['id']}"
    ends = owner_client.post(f"{url}/extend", {"days": 7}, headers=ADMIN).json()["ends_at"]
    assert ends > body["ends_at"]
    premium = make_plan(code="premium")
    changed = owner_client.post(f"{url}/change-plan", {"plan_id": str(premium.pk)}, headers=ADMIN)
    assert changed.json()["plan"]["code"] == "premium"
    assert owner_client.post(f"{url}/suspend", {"reason": "x"}, headers=ADMIN).json()["status"] == (
        "suspended"
    )
    assert owner_client.post(f"{url}/resume", headers=ADMIN).json()["status"] == "active"
    cancelled = owner_client.post(f"{url}/cancel", {"reason": "asked"}, headers=ADMIN).json()
    assert cancelled["status"] == "cancelled"
    assert owner_client.post(f"{url}/cancel", headers=ADMIN).status_code == 409
    assert owner_client.get(url, headers=ADMIN).json()["end_reason"] == "cancelled"


def test_subscription_requests_are_validated(
    owner_client: APIClient, plan: Plan, staff_user: User, customer: User
) -> None:
    missing = owner_client.post(
        f"{BASE}/subscriptions",
        {"user_id": str(staff_user.pk), "plan_id": str(plan.pk)},
        headers=ADMIN,
    )
    assert missing.status_code == 404  # staff are not customers
    unknown = owner_client.post(
        f"{BASE}/subscriptions",
        {"user_id": str(customer.pk), "plan_id": "0190f2c6-0000-7000-8000-000000000000"},
        headers=ADMIN,
    )
    assert unknown.status_code == 404
    bad = owner_client.post(
        f"{BASE}/subscriptions",
        {"user_id": str(customer.pk), "plan_id": str(plan.pk), "source": "payment"},
        headers=ADMIN,
    )
    assert bad.status_code == 400


def test_subscription_list_filters_and_queries(
    owner_client: APIClient,
    plan: Plan,
    make_customer: CustomerFactory,
    django_assert_num_queries: Capture,
) -> None:
    for _ in range(3):
        subscriptions.activate(make_customer(), plan, source="manual")
    soon = subscriptions.activate(make_customer(name="Soon Ends"), plan, source="manual")
    Subscription.objects.filter(pk=soon.pk).update(ends_at=timezone.now() + timedelta(days=2))
    with django_assert_num_queries(3):  # roles, count, page with users, plans and creators
        page = owner_client.get(f"{BASE}/subscriptions", headers=ADMIN).json()
    assert page["count"] == 4
    expiring = owner_client.get(
        f"{BASE}/subscriptions?expiring_within_days=7", headers=ADMIN
    ).json()
    assert [row["id"] for row in expiring["results"]] == [str(soon.pk)]
    searched = owner_client.get(f"{BASE}/subscriptions?search=Soon", headers=ADMIN).json()
    assert searched["count"] == 1
    statuses = owner_client.get(
        f"{BASE}/subscriptions?status=active&status=grace", headers=ADMIN
    ).json()
    assert statuses["count"] == 4


def test_trials_from_the_admin(owner_client: APIClient, trial_plan: Plan, customer: User) -> None:
    request = subscriptions.request_trial(customer, trial_plan)
    approved = owner_client.post(f"{BASE}/subscriptions/{request.pk}/approve", headers=ADMIN)
    assert approved.json()["status"] == "active"
    other = owner_client.post(
        f"{BASE}/subscriptions/trial",
        {"user_id": str(customer.pk), "plan_id": str(trial_plan.pk)},
        headers=ADMIN,
    )
    assert other.status_code == 409
    assert other.json()["code"] == "TRIAL_NOT_ELIGIBLE"


# --- Payments and invoices ----------------------------------------------------------------------


def test_record_a_manual_payment_then_refund(
    owner_client: APIClient, plan: Plan, customer: User
) -> None:
    response = owner_client.post(
        f"{BASE}/payments",
        {
            "user_id": str(customer.pk),
            "plan_id": str(plan.pk),
            "method": "bank_transfer",
            "reference": "SNB-55821",
            "idempotency_key": "form-1",
        },
        headers=ADMIN,
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["invoice_number"].startswith("INV-")
    assert body["recorded_by"] == "owner"
    assert body["webhook_events"] == []
    again = owner_client.post(
        f"{BASE}/payments",
        {"user_id": str(customer.pk), "plan_id": str(plan.pk), "idempotency_key": "form-1"},
        headers=ADMIN,
    )
    assert again.json()["id"] == body["id"]
    assert Payment.objects.count() == 1
    refunded = owner_client.post(
        f"{BASE}/payments/{body['id']}/refund",
        {"amount": 1000, "reason": "partial", "cancel_subscription": False},
        headers=ADMIN,
    )
    assert refunded.json()["refunded_amount"] == 1000
    assert refunded.json()["status"] == "partially_refunded"
    too_much = owner_client.post(
        f"{BASE}/payments/{body['id']}/refund", {"amount": 99999}, headers=ADMIN
    )
    assert too_much.status_code == 400


def test_manual_payment_needs_a_plan_or_an_invoice(owner_client: APIClient, customer: User) -> None:
    response = owner_client.post(f"{BASE}/payments", {"user_id": str(customer.pk)}, headers=ADMIN)
    assert response.status_code == 400
    assert "plan_id" in response.json()["field_errors"]


def test_payment_and_invoice_lists(
    owner_client: APIClient,
    plan: Plan,
    make_customer: CustomerFactory,
    django_assert_num_queries: Capture,
) -> None:
    for _ in range(3):
        services.record_manual_payment(user=make_customer(), plan=plan, actor=None)
    with django_assert_num_queries(3):
        payments = owner_client.get(f"{BASE}/payments", headers=ADMIN).json()
    assert payments["count"] == 3
    with django_assert_num_queries(3):
        invoices = owner_client.get(f"{BASE}/invoices", headers=ADMIN).json()
    assert invoices["count"] == 3
    number = invoices["results"][0]["number"]
    assert owner_client.get(f"{BASE}/invoices?search={number}", headers=ADMIN).json()["count"] == 1
    assert owner_client.get(f"{BASE}/payments?provider=stripe", headers=ADMIN).json()["count"] == 0
    detail = owner_client.get(f"{BASE}/invoices/{invoices['results'][0]['id']}", headers=ADMIN)
    assert detail.json()["number"] == number


def test_invoice_document(owner_client: APIClient, plan: Plan, customer: User) -> None:
    invoice = services.record_manual_payment(user=customer, plan=plan, actor=None).invoice
    assert invoice is not None
    response = owner_client.get(f"{BASE}/invoices/{invoice.pk}/document?locale=en", headers=ADMIN)
    assert response.status_code == 200
    assert response["Content-Type"] == "text/html; charset=utf-8"
    assert response["Cache-Control"] == "private, no-store"
    assert "default-src 'none'" in response["Content-Security-Policy"]
    assert f'filename="{invoice.number}.html"' in response["Content-Disposition"]
    assert "Tax invoice" in response.content.decode()


# --- KPIs ---------------------------------------------------------------------------------


def test_billing_kpis(
    owner_client: APIClient,
    plan: Plan,
    trial_plan: Plan,
    make_customer: CustomerFactory,
    owner: User,
) -> None:
    services.record_manual_payment(user=make_customer(), plan=plan, actor=None)
    services.record_manual_payment(user=make_customer(), plan=plan, actor=None, amount=4000)
    graceful = subscriptions.activate(make_customer(), plan, source="manual")
    Subscription.objects.filter(pk=graceful.pk).update(
        status=SubscriptionStatus.GRACE,
        starts_at=timezone.now() - timedelta(days=31),
        ends_at=timezone.now() - timedelta(days=1),
    )
    subscriptions.request_trial(make_customer(), trial_plan, actor=owner)
    subscriptions.request_trial(make_customer(), trial_plan)
    kpis.invalidate()
    body = owner_client.get(f"{BASE}/dashboard/billing", headers=ADMIN).json()
    assert body["active_subscribers"] == 3
    assert body["grace"] == 1
    assert body["trials_active"] == 1
    assert body["trial_requests"] == 1
    assert body["mrr"] == [{"currency": "SAR", "amount": 14700}]
    assert body["mrr_net"] == [{"currency": "SAR", "amount": 12783}]
    assert body["revenue_mtd"] == [{"currency": "SAR", "amount": 8900}]
    assert body["new_subscriptions_30d"] == 3
    assert {row["code"]: row["subscribers"] for row in body["by_plan"]} == {
        "standard": 3,
        "trial": 1,
    }
    # Cached for 30 s: a new payment shows after the cache expires.
    services.record_manual_payment(user=make_customer(), plan=plan, actor=None)
    cached = owner_client.get(f"{BASE}/dashboard/billing", headers=ADMIN).json()
    assert cached["revenue_mtd"] == body["revenue_mtd"]
