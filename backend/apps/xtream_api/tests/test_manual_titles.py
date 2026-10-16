"""A manual title (ADR-0026) through player_api.php and get.php: no TMDB id, no artwork
yet, an adult category. The PHP-panel types still hold (SPEC §7.5): `""` for the TMDB
id, the images and the empty facts, `[]` for the backdrops, checked against compat/."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from django.test import Client
from pytest_django import Settings

from apps.catalog.models import Category, CategoryKind, FileState, MatchReview, MediaFile
from apps.library.models import Library
from apps.library.parsing import parse_path
from apps.metadata import services
from apps.xtream_api.tests.conftest import Subscriber
from apps.xtream_api.tests.contract import Contract

pytestmark = pytest.mark.django_db
API = "/player_api.php"


@pytest.fixture(autouse=True)
def _media_origin(settings: Settings) -> None:
    settings.MEDIA_BASE_URL = "https://media.example.test"


@pytest.fixture(autouse=True)
def _no_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.media import tasks  # noqa: PLC0415

    for name in ("store_title_image", "frame_artwork", "prepare_media_file"):
        monkeypatch.setattr(getattr(tasks, name), "delay", lambda *_a, **_k: None)


def _get(tv: Client, subscriber: Subscriber, **params: str) -> Any:
    response = tv.get(API, subscriber.params(**params))
    assert response.status_code == 200, response.content
    return bytes(response.content)


def manual_movie(library: Library, key: str, category: Category, **fields: Any) -> Any:
    file = MediaFile.objects.create(
        library=library,
        storage_key=key,
        parse_result=parse_path(key, library_kind="movies").to_json(),
        state=FileState.REVIEW,
        container="mp4",
        duration_ms=5_400_000,
        video_codec="h264",
        width=1920,
        height=1080,
        direct_play=True,
    )
    review = MatchReview.objects.create(media_file=file, kind="movie", reason="no_candidates")
    data = {
        "title": key.rsplit(".", 1)[0],
        "rights_holder": "Family archive",
        "license_ref": "OWN-1",
        "rights_confirmed": True,
        "categories": [category],
        **fields,
    }
    return services.create_manual_movie(file=file, review=review, data=data, actor=None, ip=None)


def test_a_manual_title_keeps_the_xtream_types(
    tv: Client,
    subscriber: Subscriber,
    contract: Contract,
    make_library: Callable[..., Library],
) -> None:
    library = make_library()
    family = Category.objects.create(
        kind=CategoryKind.VOD, slug="family", name_en="Family", name_ar="عائلي"
    )
    adult = Category.objects.create(
        kind=CategoryKind.VOD, slug="late", name_en="Late night", name_ar="ليلي", is_adult=True
    )
    bare = manual_movie(library, "Home Video.mp4", adult)
    full = manual_movie(
        library,
        "Our Trip.mp4",
        family,
        title_ar="رحلتنا",
        overview="A trip.",
        year=2025,
        certification="G",
        countries=["SA"],
        trailer_youtube_key="dQw4w9WgXcQ",
    )

    categories = contract.check(
        "get_vod_categories", _get(tv, subscriber, action="get_vod_categories")
    )
    streams = contract.check("get_vod_streams", _get(tv, subscriber, action="get_vod_streams"))
    by_id = {row["stream_id"]: row for row in streams}
    assert set(by_id) == {bare.xc_id, full.xc_id}
    row = by_id[bare.xc_id]
    assert (row["tmdb_id"], row["stream_icon"], row["rating"], row["rating_5based"]) == (
        "",
        "",
        "",
        0,
    )
    # ADR-0011: apps get adult categories when the plan allows every category, flagged
    # for their own parental lock; the portal hides them unless granted (ADR-0013).
    assert (row["is_adult"], by_id[full.xc_id]["is_adult"]) == ("1", "0")
    assert {c["category_id"] for c in categories} == {str(family.xc_id), str(adult.xc_id)}

    infos = {}
    for movie in (bare, full):
        body = _get(tv, subscriber, action="get_vod_info", vod_id=str(movie.xc_id))
        infos[movie.xc_id] = contract.check("get_vod_info", body)
    info = infos[bare.xc_id]["info"]
    assert (info["tmdb_id"], info["imdb_id"], info["cover_big"], info["movie_image"]) == (
        "",
        "",
        "",
        "",
    )
    assert info["backdrop_path"] == []
    assert (info["cast"], info["director"], info["genre"], info["youtube_trailer"]) == (
        "",
        "",
        "",
        "",
    )
    assert info["duration_secs"] == 5400
    rich = infos[full.xc_id]["info"]
    assert (rich["year"], rich["age"], rich["youtube_trailer"]) == ("2025", "G", "dQw4w9WgXcQ")

    assert not contract.check_catalog(
        {
            "get_vod_categories": categories,
            "get_vod_streams": streams,
            "get_series_categories": [],
            "get_series": [],
            "get_live_categories": [],
            "get_live_streams": [],
        },
        [],
    )
    playlist = tv.get("/get.php", subscriber.params(type="m3u_plus", output="ts"))
    login = json.loads(_get(tv, subscriber))["server_info"]
    origin = f"{login['server_protocol']}://{login['url']}:{login['port']}"
    _parsed, problems = contract.check_m3u(
        playlist.content,
        live_ext="ts",
        origin=origin,
        credentials=(subscriber.username, subscriber.password),
    )
    assert not problems
