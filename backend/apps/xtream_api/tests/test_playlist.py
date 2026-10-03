"""get.php (compat/m3u.md) and xmltv.php (compat/xmltv.md) through the full stack."""

import gzip
from typing import Any

import pytest
from django.test import Client

from apps.core.services import set_setting
from apps.xtream_api import playlist
from apps.xtream_api.auth import server_info
from apps.xtream_api.tests.conftest import Subscriber
from apps.xtream_api.tests.contract import Contract
from apps.xtream_api.tests.fakes import InMemoryCatalogSource

pytestmark = pytest.mark.django_db


def catalog_payloads(tv: Client, subscriber: Subscriber) -> tuple[dict[str, Any], list[Any]]:
    payloads = {
        action: tv.get("/player_api.php", subscriber.params(action=action)).json()
        for action in (
            "get_vod_categories",
            "get_vod_streams",
            "get_series_categories",
            "get_series",
        )
    }
    infos = [
        tv.get(
            "/player_api.php",
            subscriber.params(action="get_series_info", series_id=str(item["series_id"])),
        ).json()
        for item in payloads["get_series"]
    ]
    return payloads, infos


@pytest.mark.parametrize(
    ("output", "live_ext"),
    [("ts", "ts"), ("m3u8", "m3u8"), ("mp4", "ts"), ("hls", "m3u8"), ("", "ts")],
)
def test_playlist_matches_the_contract_and_the_catalog(
    *,
    tv: Client,
    subscriber: Subscriber,
    catalog: InMemoryCatalogSource,
    contract: Contract,
    output: str,
    live_ext: str,
) -> None:
    response = tv.get("/get.php", subscriber.params(type="m3u_plus", output=output))
    assert response.status_code == 200
    assert response["Content-Type"] == "audio/x-mpegurl; charset=utf-8"
    assert response["Content-Disposition"] == 'attachment; filename="playlist.m3u"'
    playlist, problems = contract.check_m3u(
        response.content,
        live_ext=live_ext,
        origin=server_info().origin,
        credentials=(subscriber.username, subscriber.password),
    )
    assert not problems, problems
    payloads, infos = catalog_payloads(tv, subscriber)
    assert not contract.check_catalog(payloads, infos, playlist)
    assert [(entry.kind, entry.xc_id) for entry in playlist.entries] == [
        ("movie", 1001),
        ("movie", 1002),
        ("series", 5003),
        ("series", 5001),
        ("series", 5002),
    ]


def test_playlist_post_and_gzip(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource
) -> None:
    response = tv.post(
        "/get.php", subscriber.params(type="m3u_plus"), headers={"accept-encoding": "gzip"}
    )
    assert response["Content-Encoding"] == "gzip"
    assert gzip.decompress(response.content).startswith(b"#EXTM3U url-tvg=")


def test_playlist_without_vod_when_the_setting_is_off(
    tv: Client,
    subscriber: Subscriber,
    catalog: InMemoryCatalogSource,
    contract: Contract,
    django_capture_on_commit_callbacks: Any,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        set_setting("features.include_vod_in_m3u", False, actor=None)
    response = tv.get("/get.php", subscriber.params(type="m3u_plus", output="ts"))
    lines = response.content.decode().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("#EXTM3U url-tvg=")
    _, problems = contract.check_m3u(
        response.content,
        live_ext="ts",
        origin=server_info().origin,
        credentials=(subscriber.username, subscriber.password),
    )
    assert not problems


def test_inactive_accounts_get_an_empty_playlist(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource
) -> None:
    subscriber.user.status = "suspended"
    subscriber.user.save(update_fields=["status"])
    response = tv.get("/get.php", subscriber.params())
    assert response.status_code == 200
    assert len(response.content.decode().splitlines()) == 1
    assert catalog.calls.total() == 0


@pytest.mark.parametrize("path", ["/get.php", "/xmltv.php"])
def test_failed_authentication_is_an_empty_403(
    tv: Client, subscriber: Subscriber, path: str
) -> None:
    wrong = tv.get(path, {"username": subscriber.username, "password": "wrong-password-1"})
    unknown = tv.get(path, {"username": "nobody-k3p9qa", "password": "wrong-password-1"})
    for response in (wrong, unknown):
        assert (response.status_code, response.content) == (403, b"")


def test_guide_is_a_valid_empty_xmltv(
    tv: Client, subscriber: Subscriber, contract: Contract
) -> None:
    response = tv.get("/xmltv.php", subscriber.params())
    assert response.status_code == 200
    assert response["Content-Type"] == "application/xml; charset=utf-8"
    assert not contract.check_xmltv(response.content)
    assert b"<channel" not in response.content


def test_playlist_credentials_are_encoded(
    tv: Client, subscriber: Subscriber, catalog: InMemoryCatalogSource, contract: Contract
) -> None:
    body = playlist.render(
        [playlist.Entry("movie", 1001, 'Say "hi"\nthere', "", "Group, with comma")],
        origin="https://tv.example.com",
        username="ab c",
        password="p/w?&=",  # noqa: S106
        live_ext="ts",
    )
    text = body.decode()
    assert (
        '#EXTM3U url-tvg="https://tv.example.com/xmltv.php?username=ab+c&password=p%2Fw%3F%26%3D"'
        in text
    )
    assert "https://tv.example.com/movie/ab%20c/p%2Fw%3F%26%3D/1001.mp4" in text
    assert "tvg-name=\"Say 'hi' there\"" in text
    _, problems = contract.check_m3u(
        body, live_ext="ts", origin="https://tv.example.com", credentials=("ab c", "p/w?&=")
    )
    assert not problems
