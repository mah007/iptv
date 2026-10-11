"""The Integrations page's Test for Stripe and Moyasar (CFG, ADR-0019): the saved key is
tried against the provider, and our webhook's registration is reported."""

import httpx
import pytest

from apps.billing.providers.base import use_transport
from apps.billing.providers.moyasar import MoyasarProvider
from apps.billing.providers.stripe import StripeProvider
from apps.core.integration import TestCode
from apps.core.services import set_setting

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_server_config")]

# Built from parts so secret scanners (GitHub push protection) see no key-shaped literal.
STRIPE_KEY = "sk_" + "test_" + "51ABCDEFGHIJKLMNOPQRSTUV"
MOYASAR_KEY = "sk_" + "test_ABCDEFGHIJKLMNOP"
HOOK = "https://api.example.com/api/v1/webhooks/stripe"


def provider_api(
    routes: dict[str, httpx.Response],
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return routes[request.url.path]

    return httpx.MockTransport(handler), seen


def test_stripe_accepts_the_key_and_finds_our_webhook() -> None:
    set_setting("billing.stripe_secret_key", STRIPE_KEY, actor=None)
    transport, seen = provider_api(
        {
            "/v1/balance": httpx.Response(200, json={"object": "balance", "livemode": False}),
            "/v1/webhook_endpoints": httpx.Response(
                200, json={"data": [{"url": HOOK, "status": "enabled"}]}
            ),
        }
    )
    with use_transport(transport):
        result = StripeProvider().check(HOOK)
    assert result.ok is True
    assert result.details == {
        "mode": "test",
        "webhook_url": HOOK,
        "webhook_registered": "yes",
        "webhook_secret_set": False,
    }
    assert seen[0].headers["authorization"] == f"Bearer {STRIPE_KEY}"
    assert STRIPE_KEY not in repr(result)


def test_stripe_without_our_webhook_or_with_a_restricted_key() -> None:
    set_setting("billing.stripe_secret_key", STRIPE_KEY, actor=None)
    for answer, registered in (
        (httpx.Response(200, json={"data": [{"url": "https://other", "status": "enabled"}]}), "no"),
        (httpx.Response(403, json={"error": {"message": "restricted"}}), "unknown"),
    ):
        transport, _ = provider_api(
            {
                "/v1/balance": httpx.Response(200, json={"livemode": True}),
                "/v1/webhook_endpoints": answer,
            }
        )
        with use_transport(transport):
            result = StripeProvider().check(HOOK)
        assert (result.details["mode"], result.details["webhook_registered"]) == (
            "live",
            registered,
        )


def test_stripe_refusals_and_outages() -> None:
    assert StripeProvider().check(HOOK).code is TestCode.NOT_CONFIGURED
    set_setting("billing.stripe_secret_key", STRIPE_KEY, actor=None)
    transport, _ = provider_api(
        {
            "/v1/balance": httpx.Response(
                401, json={"error": {"message": f"Invalid API Key provided: {STRIPE_KEY}"}}
            )
        }
    )
    with use_transport(transport):
        result = StripeProvider().check(HOOK)
    assert result.code is TestCode.AUTH_FAILED
    assert STRIPE_KEY not in result.message

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with use_transport(httpx.MockTransport(down)):
        assert StripeProvider().check(HOOK).code is TestCode.TIMEOUT


def test_moyasar_accepts_the_key_and_finds_our_webhook() -> None:
    hook = "https://api.example.com/api/v1/webhooks/moyasar"
    set_setting("billing.moyasar_secret_key", MOYASAR_KEY, actor=None)
    set_setting("billing.moyasar_webhook_secret", "moyasar-secret-token", actor=None)
    transport, seen = provider_api(
        {
            "/v1/payments": httpx.Response(200, json={"payments": []}),
            "/v1/webhooks": httpx.Response(200, json={"webhooks": [{"url": hook}]}),
        }
    )
    with use_transport(transport):
        result = MoyasarProvider().check(hook)
    assert result.ok is True
    assert result.details["webhook_registered"] == "yes"
    assert result.details["webhook_secret_set"] is True
    assert seen[0].headers["authorization"].startswith("Basic ")


def test_moyasar_refusals() -> None:
    assert MoyasarProvider().check("x").code is TestCode.NOT_CONFIGURED
    set_setting("billing.moyasar_secret_key", MOYASAR_KEY, actor=None)
    transport, _ = provider_api({"/v1/payments": httpx.Response(401, json={"type": "auth"})})
    with use_transport(transport):
        assert MoyasarProvider().check("x").code is TestCode.AUTH_FAILED


def test_providers_follow_the_settings(staff_user: object) -> None:
    provider = StripeProvider()
    assert provider.is_configured() is False
    set_setting("billing.stripe_secret_key", STRIPE_KEY, actor=None)
    set_setting("billing.stripe_webhook_secret", "whsec_" + "ABCDEFGHIJKL", actor=None)
    assert provider.is_configured() is True
    assert provider.is_enabled() is False
    set_setting("billing.stripe_enabled", True, actor=None)
    assert provider.is_enabled() is True
