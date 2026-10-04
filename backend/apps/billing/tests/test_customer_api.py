"""Customer billing endpoints on api.<domain> and app.<domain>: plans, checkout,
my subscription and invoices."""

from typing import Any

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing import services, subscriptions
from apps.billing.models import InvoiceStatus, Plan
from apps.billing.tests.conftest import PlanFactory, Recorded, configure, fixture_json
from apps.conftest import CustomerFactory

pytestmark = pytest.mark.django_db

API = {"host": settings.API_HOST}
PORTAL = {"host": settings.APP_HOST}


@pytest.fixture
def customer(make_customer: CustomerFactory) -> User:
    return make_customer(devices=1)


def client_for(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def test_plans_are_public_and_on_sale_only(
    plan: Plan, trial_plan: Plan, make_plan: PlanFactory
) -> None:
    make_plan(code="retired", active=False)
    for host in (API, PORTAL):
        response = APIClient().get("/api/v1/plans", headers=host)
        assert response.status_code == 200
        rows = response.json()
        assert [row["code"] for row in rows] == ["standard", "trial"]
        assert rows[0]["price"]["total"] == 4900
        assert rows[0]["price"]["vat"] == 639
        assert rows[1]["is_trial"] is True
        assert "version" not in rows[0]


def test_customer_endpoints_need_a_signed_in_customer(staff_user: User) -> None:
    for path in ("/api/v1/me/subscription", "/api/v1/me/invoices", "/api/v1/payment-providers"):
        assert APIClient().get(path, headers=API).status_code == 401
        assert client_for(staff_user).get(path, headers=API).status_code == 403


def test_providers_offered(customer: User, provider_keys: Any) -> None:
    codes = [
        row["code"]
        for row in client_for(customer).get("/api/v1/payment-providers", headers=API).json()
    ]
    assert codes == ["manual"]
    configure("billing.moyasar_enabled", True)
    codes = [
        row["code"]
        for row in client_for(customer).get("/api/v1/payment-providers", headers=API).json()
    ]
    assert codes == ["manual", "moyasar"]


def test_manual_checkout(customer: User, plan: Plan) -> None:
    response = client_for(customer).post(
        "/api/v1/checkout", {"plan_id": str(plan.pk), "provider": "manual"}, headers=API
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["kind"] == "manual"
    assert body["provider"] == "manual"
    assert body["redirect_url"] is None
    assert body["reference"].startswith("P-")
    assert body["instructions"]["ar"]
    assert body["invoice"]["status"] == "pending"
    assert body["invoice"]["total"] == 4900
    assert body["invoice"]["number"] is None


def test_stripe_checkout_redirects(
    customer: User, plan: Plan, provider_keys: Any, recorded: Recorded
) -> None:
    configure("billing.stripe_enabled", True)
    recorded.add(
        "POST",
        "https://api.stripe.com/v1/checkout/sessions",
        200,
        fixture_json("stripe_checkout_session.json"),
    )
    body = (
        client_for(customer)
        .post("/api/v1/checkout", {"plan_id": str(plan.pk), "provider": "stripe"}, headers=PORTAL)
        .json()
    )
    assert body["kind"] == "redirect"
    assert body["redirect_url"].startswith("https://checkout.stripe.com/")


def test_checkout_refusals(customer: User, plan: Plan, make_plan: PlanFactory) -> None:
    client = client_for(customer)
    disabled = client.post(
        "/api/v1/checkout", {"plan_id": str(plan.pk), "provider": "stripe"}, headers=API
    )
    assert disabled.status_code == 503
    assert disabled.json()["code"] == "PAYMENT_PROVIDER_UNAVAILABLE"
    retired = make_plan(active=False)
    assert (
        client.post("/api/v1/checkout", {"plan_id": str(retired.pk)}, headers=API).status_code
        == 404
    )
    foreign = client.post(
        "/api/v1/checkout",
        {"plan_id": str(plan.pk), "return_url": "https://phish.example.com/"},
        headers=API,
    )
    assert foreign.status_code == 400


def test_trial_through_checkout(customer: User, trial_plan: Plan) -> None:
    body = (
        client_for(customer)
        .post("/api/v1/checkout", {"plan_id": str(trial_plan.pk)}, headers=API)
        .json()
    )
    assert body["kind"] == "trial"
    assert body["subscription"]["status"] == "pending"
    assert body["invoice"] is None
    mine = client_for(customer).get("/api/v1/me/subscription", headers=API).json()
    assert mine["subscription"] is None
    assert mine["pending"]["source"] == "trial"
    again = client_for(customer).post(
        "/api/v1/checkout", {"plan_id": str(trial_plan.pk)}, headers=API
    )
    assert again.json()["subscription"]["id"] == body["subscription"]["id"]


def test_my_subscription_and_invoices(
    customer: User, plan: Plan, make_customer: CustomerFactory
) -> None:
    client = client_for(customer)
    assert client.get("/api/v1/me/subscription", headers=API).json() == {
        "subscription": None,
        "pending": None,
    }
    payment = services.record_manual_payment(user=customer, plan=plan, actor=None)
    services.checkout(customer, plan, "manual")  # awaiting payment
    awaiting = services.checkout(customer, plan, "manual").invoice  # voids the one before
    other = services.record_manual_payment(user=make_customer(), plan=plan, actor=None)
    mine = client.get("/api/v1/me/subscription", headers=API).json()["subscription"]
    assert mine["status"] == "active"
    assert mine["max_streams"] == 2
    assert mine["plan"]["code"] == "standard"
    invoices = client.get("/api/v1/me/invoices", headers=API).json()
    statuses = sorted(row["status"] for row in invoices["results"])
    assert statuses == [InvoiceStatus.PAID, InvoiceStatus.PENDING]
    assert awaiting is not None
    assert str(awaiting.pk) in {row["id"] for row in invoices["results"]}
    assert payment.invoice is not None
    document = client.get(f"/api/v1/me/invoices/{payment.invoice.pk}/document", headers=PORTAL)
    assert document.status_code == 200
    assert "فاتورة ضريبية" in document.content.decode()
    assert other.invoice is not None
    assert (
        client.get(f"/api/v1/me/invoices/{other.invoice.pk}/document", headers=API).status_code
        == 404
    )


def test_an_ended_subscription_still_shows(customer: User, plan: Plan) -> None:
    subscriptions.cancel(subscriptions.activate(customer, plan, source="manual"), actor=None)
    mine = client_for(customer).get("/api/v1/me/subscription", headers=API).json()
    assert mine["subscription"]["status"] == "cancelled"
