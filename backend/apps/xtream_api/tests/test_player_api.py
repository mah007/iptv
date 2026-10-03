"""player_api.php through the full stack on the tv host, checked against compat/ (SPEC §7.5)."""

import json
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone

from apps.accounts import services as account_services
from apps.accounts.models import Device, UserStatus
from apps.catalog.models import Category, CategoryKind
from apps.core.services import set_setting
from apps.playback import entitlements
from apps.xtream_api.source import set_catalog_source
from apps.xtream_api.tests.conftest import Subscriber
from apps.xtream_api.tests.contract import Contract
from apps.xtream_api.tests.fakes import FakeStarter, InMemoryCatalogSource, sample_catalog

pytestmark = pytest.mark.django_db
API = "/player_api.php"


def call(tv: Client, subscriber: Subscriber, method: str = "GET", **params: str) -> Any:
    data = subscriber.params(**params)
    response = tv.post(API, data) if method == "POST" else tv.get(API, data)
    assert response.status_code == 200, response.content
    assert response["Content-Type"] == "application/json"
    return response


def payload(tv: Client, subscriber: Subscriber, **params: str) -> Any:
    return json.loads(call(tv, subscriber, **params).content)


# --- Login -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("GET", {}),
        ("POST", {}),
        ("GET", {"action": "get_account_info"}),
        ("POST", {"action": "get_account_info"}),
        ("GET", {"action": "compat_unknown_action"}),
    ],
)
def test_login_payload(
    *,
    tv: Client,
    subscriber: Subscriber,
    starter: FakeStarter,
    contract: Contract,
    method: str,
    params: dict[str, str],
) -> None:
    starter.active = 1
    login = contract.check("login", call(tv, subscriber, method, **params).content)
    user_info, server_info = login["user_info"], login["server_info"]
    assert (user_info["username"], user_info["password"]) == (
        subscriber.username,
        subscriber.password,
    )
    assert (user_info["status"], user_info["max_connections"]) == ("Active", "2")
    assert user_info["active_cons"] == "1"
    assert user_info["created_at"] == str(int(subscriber.user.created_at.timestamp()))
    assert server_info["url"] == settings.TV_HOST
    assert server_info["server_protocol"] == settings.PUBLIC_SCHEME
    port = server_info["https_port" if settings.PUBLIC_SCHEME == "https" else "port"]
    assert port == str(settings.PUBLIC_PORT)


def test_credentials_in_the_form_body_win_over_the_query(
    tv: Client, subscriber: Subscriber, contract: Contract
) -> None:
    response = tv.post(f"{API}?username=someone-else&password=wrong-password", subscriber.params())
    login = contract.check("login", response.content)
    assert login["user_info"]["username"] == subscriber.username


def test_server_url_setting_wins(
    tv: Client,
    subscriber: Subscriber,
    contract: Contract,
    django_capture_on_commit_callbacks: Any,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        set_setting("xtream.server_url", "https://iptv.example.net", actor=None)
        set_setting("xtream.port", 8880, actor=None)
    server_info = contract.check("login", call(tv, subscriber).content)["server_info"]
    assert (server_info["url"], server_info["server_protocol"]) == ("iptv.example.net", "https")
    assert (server_info["port"], server_info["https_port"]) == ("8880", "443")


def test_expired_access_reports_expired_with_its_end_date(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource, contract: Contract
) -> None:
    ended = timezone.now() - timedelta(days=1)
    account_services.update_access(subscriber.user, {"expires_at": ended}, actor=None)
    entitlements.refresh(subscriber.user.pk)
    user_info = contract.check("login", call(tv, subscriber).content)["user_info"]
    assert user_info["status"] == "Expired"
    assert user_info["exp_date"] == str(int(ended.timestamp()))
    # The account signs in, but sees nothing.
    assert payload(tv, subscriber, action="get_vod_categories") == []
    assert payload(tv, subscriber, action="get_series") == []
    response = tv.get(API, subscriber.params(action="get_vod_info", vod_id="1001"))
    assert (response.status_code, response.content) == (404, b"{}")
    assert catalog.calls.total() == 0


@pytest.mark.parametrize("change", ["suspended", "disabled", "blocked", "unapproved"])
def test_accounts_and_devices_that_may_not_watch_are_disabled(
    tv: Client,
    subscriber: Subscriber,
    catalog: InMemoryCatalogSource,
    contract: Contract,
    change: str,
) -> None:
    if change in ("suspended", "disabled"):
        subscriber.user.status = UserStatus(change)
        subscriber.user.save(update_fields=["status"])
    elif change == "blocked":
        Device.objects.filter(pk=subscriber.device.pk).update(blocked=True)
    else:
        Device.objects.filter(pk=subscriber.device.pk).update(approved=False)
    entitlements.refresh(subscriber.user.pk)
    login = contract.check("login", call(tv, subscriber).content)
    assert login["user_info"]["status"] == "Disabled"
    assert payload(tv, subscriber, action="get_vod_streams") == []


def test_customer_without_an_end_date_gets_a_far_future_one(
    tv: Client, subscriber: Subscriber, contract: Contract
) -> None:
    user_info = contract.check("login", call(tv, subscriber).content)["user_info"]
    assert user_info["exp_date"] == "4102444800"


# --- Failed authentication ---------------------------------------------------------------------


def test_auth_failures_are_identical_http_200(
    tv: Client, subscriber: Subscriber, contract: Contract
) -> None:
    attempts = [
        tv.get(API, {"username": subscriber.username, "password": "wrong-password-1"}),
        tv.get(API, {"username": "nobody-x1y2z3", "password": "wrong-password-1"}),
        tv.post(
            API,
            {
                "username": subscriber.username,
                "password": "wrong-password-1",
                "action": "get_vod_streams",
            },
        ),
        tv.get(API, {"username": "", "password": ""}),
        tv.get(API),
        tv.get(API, {"username": "nul\x00byte", "password": "x"}),
    ]
    for response in attempts:
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        assert response.content == b'{"user_info":{"auth":0}}'
        contract.check("auth_failure", response.content)


def test_revoked_devices_fail_like_unknown_users(tv: Client, subscriber: Subscriber) -> None:
    account_services.revoke_device(subscriber.device, actor=None)
    assert call(tv, subscriber).content == b'{"user_info":{"auth":0}}'


# --- Catalog actions -----------------------------------------------------------------------------


def test_every_action_matches_the_contract(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource, contract: Contract
) -> None:
    responses: dict[str, Any] = {}
    for action in (
        "get_vod_categories",
        "get_series_categories",
        "get_live_categories",
        "get_vod_streams",
        "get_series",
        "get_live_streams",
    ):
        responses[action] = contract.check(action, call(tv, subscriber, action=action).content)
    responses["get_vod_info"] = contract.check(
        "get_vod_info", call(tv, subscriber, action="get_vod_info", vod_id="1001").content
    )
    series_info = contract.check(
        "get_series_info", call(tv, subscriber, action="get_series_info", series_id="2001").content
    )
    for action in ("get_short_epg", "get_simple_data_table"):
        body = call(tv, subscriber, action=action, stream_id="3001", limit="4").content
        assert contract.check(action, body) == {"epg_listings": []}

    assert [item["stream_id"] for item in responses["get_vod_streams"]] == [1001, 1002]
    assert [item["series_id"] for item in responses["get_series"]] == [2001]
    assert responses["get_vod_info"]["movie_data"]["stream_id"] == 1001
    assert list(series_info["episodes"]) == ["0", "1"]
    assert responses["get_live_categories"] == responses["get_live_streams"] == []
    assert not contract.check_catalog(responses, [series_info])


def test_category_filter(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource, contract: Contract
) -> None:
    sci_fi = payload(tv, subscriber, action="get_vod_streams", category_id="12")
    contract.check("get_vod_streams", json.dumps(sci_fi).encode())
    assert [(item["num"], item["stream_id"]) for item in sci_fi] == [(1, 1001)]
    documentaries = payload(tv, subscriber, action="get_vod_streams", category_id="14")
    assert [item["stream_id"] for item in documentaries] == [1002]
    assert [
        item["series_id"] for item in payload(tv, subscriber, action="get_series", category_id="22")
    ] == [2001]
    for unknown in ("999999", "abc", "-1", "0", "21"):
        assert payload(tv, subscriber, action="get_vod_streams", category_id=unknown) == []
    # An empty category_id means no filter, as PHP panels treat it.
    assert len(payload(tv, subscriber, action="get_vod_streams", category_id="")) == 2


@pytest.mark.parametrize(
    ("action", "param", "value"),
    [
        ("get_vod_info", "vod_id", "424242"),
        ("get_vod_info", "vod_id", "5001"),  # an episode id is not a movie
        ("get_vod_info", "vod_id", "abc"),
        ("get_vod_info", "vod_id", ""),
        ("get_series_info", "series_id", "1001"),
        ("get_series_info", "series_id", "-2"),
    ],
)
def test_unknown_ids_are_an_empty_object_with_404(
    *,
    tv: Client,
    subscriber: Subscriber,
    catalog: InMemoryCatalogSource,
    action: str,
    param: str,
    value: str,
) -> None:
    response = tv.get(API, subscriber.params(action=action, **{param: value}))
    assert (response.status_code, response.content) == (404, b"{}")
    assert response["Content-Type"] == "application/json"


def test_entitlement_scope_limits_categories_and_items(
    tv: Client, subscriber: Subscriber, contract: Contract
) -> None:
    category = Category.objects.create(
        kind=CategoryKind.VOD, slug="sci-fi", name_en="Science Fiction", name_ar="خيال علمي"
    )
    source = InMemoryCatalogSource(sample_catalog({12: str(category.pk)}))
    previous = set_catalog_source(source)
    try:
        account_services.update_access(
            subscriber.user, {"allow_series": False}, category_ids=[category.pk], actor=None
        )
        entitlements.refresh(subscriber.user.pk)
        categories = contract.check(
            "get_vod_categories", call(tv, subscriber, action="get_vod_categories").content
        )
        movies = contract.check(
            "get_vod_streams", call(tv, subscriber, action="get_vod_streams").content
        )
        assert [c["category_id"] for c in categories] == ["12"]
        assert [(m["stream_id"], m["category_ids"]) for m in movies] == [(1001, [12])]
        assert payload(tv, subscriber, action="get_series_categories") == []
        assert payload(tv, subscriber, action="get_series") == []
        hidden = tv.get(API, subscriber.params(action="get_vod_info", vod_id="1002"))
        assert hidden.status_code == 404
        assert not contract.check_catalog(
            {"get_vod_categories": categories, "get_vod_streams": movies}, []
        )
    finally:
        set_catalog_source(previous)


def test_arabic_customers_get_arabic_category_names(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource, contract: Contract
) -> None:
    subscriber.user.locale = "ar"
    subscriber.user.save(update_fields=["locale"])
    categories = contract.check(
        "get_vod_categories", call(tv, subscriber, action="get_vod_categories").content
    )
    assert [c["category_name"] for c in categories] == ["أكشن", "خيال علمي", "وثائقيات"]
    movies = payload(tv, subscriber, action="get_vod_streams")
    assert movies[0]["name"] == "ماتريكس"


def test_large_responses_are_gzipped_when_accepted(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource
) -> None:
    response = tv.get(
        API,
        subscriber.params(action="get_series_info", series_id="2001"),
        headers={"accept-encoding": "gzip"},
    )
    assert response["Content-Encoding"] == "gzip"
    assert "no-store" in response["Cache-Control"]


def test_cached_catalog_requests_cost_one_query(
    tv: Client,
    subscriber: Subscriber,
    catalog: InMemoryCatalogSource,
    django_assert_num_queries: Any,
) -> None:
    payload(tv, subscriber, action="get_vod_streams")
    with django_assert_num_queries(1):  # the credential, with its device and customer
        payload(tv, subscriber, action="get_vod_streams")


def test_unsupported_methods(tv: Client, subscriber: Subscriber) -> None:
    assert tv.put(API, subscriber.params()).status_code == 405
    assert tv.head(API, subscriber.params()).status_code == 200
