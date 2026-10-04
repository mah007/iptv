"""A small catalogue builder for the customer API tests (catalog, engagement, search)."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.catalog.models import (
    Category,
    CategoryKind,
    Credit,
    CreditRole,
    Episode,
    FileState,
    Genre,
    MediaFile,
    MediaImage,
    Movie,
    Person,
    Season,
    Series,
    TitleStatus,
)
from apps.core.stores import cache_redis, state_redis
from apps.library.models import Library
from apps.media.models import Rendition, RenditionKind, RenditionStatus
from apps.playback import entitlements

PORTAL = {"host": settings.APP_HOST}
API = {"host": settings.API_HOST}


def image_sizes(owner: str, kind: str) -> dict[str, dict[str, str]]:
    sizes = ("w780", "w1280") if kind == "backdrop" else ("w185", "w500", "original")
    return {
        size: {
            fmt: f"images/{owner}/{kind}/{size}.0123456789abcdef.{fmt}" for fmt in ("webp", "avif")
        }
        for size in sizes
    }


@dataclass
class Builder:
    library: Library
    people: dict[str, Person] = field(default_factory=dict)

    def category(
        self, slug: str, *, kind: str = CategoryKind.VOD, adult: bool = False, sort: int = 0
    ) -> Category:
        return Category.objects.create(
            kind=kind,
            slug=slug,
            name_en=slug.title(),
            name_ar=f"ع-{slug}",
            is_adult=adult,
            sort=sort,
        )

    def genre(self, name_en: str, name_ar: str = "") -> Genre:
        return Genre.objects.create(name_en=name_en, name_ar=name_ar)

    def person(self, name: str, name_ar: str = "") -> Person:
        if name not in self.people:
            self.people[name] = Person.objects.create(name=name, name_ar=name_ar)
        return self.people[name]

    def _image(self, kind: str, **owner: Any) -> MediaImage:
        ((name, value),) = owner.items()
        return MediaImage.objects.create(
            kind=kind,
            sizes=image_sizes(f"{name}{value.pk.hex[:8]}", kind),
            width=500,
            height=750,
            blurhash="LEHV6nWB2yk8pyo0adR*.7kCMdnj",
            is_primary=True,
            **owner,
        )

    def _file(self, *, playable: bool, **owner: Any) -> MediaFile:
        file = MediaFile.objects.create(
            library=self.library,
            storage_key=f"{uuid4().hex}.mp4",
            state=FileState.MATCHED,
            direct_play=False,
            height=1080,
            duration_ms=5_400_000,
            probe_summary={
                "audio": [{"language": "eng", "codec": "aac", "channels": 2, "default": True}],
                "subtitles": [{"language": "ara", "codec": "subrip", "default": False}],
            },
            **owner,
        )
        if playable:
            Rendition.objects.create(
                media_file=file,
                kind=RenditionKind.COMPAT_MP4,
                storage_key=file.pk.hex,
                status=RenditionStatus.READY,
                height=1080,
                duration_s=5400.0,
            )
        return file

    def _credits(self, credits: Iterable[tuple[str, str]], **owner: Any) -> None:
        for order, (role, name) in enumerate(credits):
            Credit.objects.create(
                person=self.person(name),
                role=role,
                character="Hero" if role == CreditRole.CAST else "",
                order=order,
                **owner,
            )

    def movie(
        self,
        title: str,
        *,
        title_ar: str = "",
        categories: Iterable[Category] = (),
        genres: Iterable[Genre] = (),
        year: int | None = 2000,
        rating: float | None = 7.0,
        popularity: float = 1.0,
        status: str = TitleStatus.READY,
        playable: bool = True,
        artwork: bool = True,
        featured: bool = False,
        cast: Iterable[str] = (),
        directors: Iterable[str] = (),
        license_expires_at: datetime | None = None,
        overview: str = "An overview.",
        overview_ar: str = "",
    ) -> Movie:
        movie = Movie.objects.create(
            title=title,
            title_ar=title_ar,
            original_title=title,
            year=year,
            rating=rating,
            popularity=popularity,
            status=status,
            featured=featured,
            runtime_min=90,
            license_expires_at=license_expires_at,
            overview=overview,
            overview_ar=overview_ar,
            original_language="en",
        )
        movie.categories.set(list(categories))
        movie.genres.set(list(genres))
        if artwork:
            self._image("poster", movie=movie)
            self._image("backdrop", movie=movie)
        self._file(playable=playable, movie=movie)
        self._credits(
            [*((CreditRole.CAST, name) for name in cast),
             *((CreditRole.DIRECTOR, name) for name in directors)],
            movie=movie,
        )  # fmt: skip
        return movie

    def series(
        self,
        title: str,
        *,
        title_ar: str = "",
        categories: Iterable[Category] = (),
        genres: Iterable[Genre] = (),
        seasons: dict[int, int] | None = None,
        unplayable: Iterable[tuple[int, int]] = (),
        status: str = TitleStatus.READY,
        popularity: float = 1.0,
        year: int | None = 2010,
        cast: Iterable[str] = (),
    ) -> Series:
        series = Series.objects.create(
            title=title,
            title_ar=title_ar,
            original_title=title,
            year=year,
            rating=8.0,
            popularity=popularity,
            status=status,
            episode_run_time=45,
            original_language="en",
        )
        series.categories.set(list(categories))
        series.genres.set(list(genres))
        self._image("poster", series=series)
        self._image("backdrop", series=series)
        skip = set(unplayable)
        for number, count in (seasons or {1: 3}).items():
            season = Season.objects.create(series=series, number=number, name=f"Season {number}")
            for episode_number in range(1, count + 1):
                episode = Episode.objects.create(
                    season=season,
                    number=episode_number,
                    title=f"Episode {episode_number}",
                    title_ar=f"الحلقة {episode_number}",
                    runtime_min=45,
                )
                self._image("still", episode=episode)
                file = self._file(playable=(number, episode_number) not in skip)
                file.episodes.add(episode)
        self._credits(((CreditRole.CAST, name) for name in cast), series=series)
        return series


def episode(series: Series, season: int, number: int) -> Episode:
    return Episode.objects.get(season__series=series, season__number=season, number=number)


def portal_client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    client.defaults["HTTP_HOST"] = PORTAL["host"]
    return client


def refresh_entitlement(user: User) -> None:
    entitlements.refresh(user.pk)


@pytest.fixture
def clean_stores() -> Iterator[None]:
    """Each test starts with empty redis-state and redis-cache (its lane's databases)."""
    state_redis().flushdb()
    cache_redis().flushdb()
    yield
    state_redis().flushdb()
    cache_redis().flushdb()


@pytest.fixture
def build(make_library: Any) -> Builder:
    return Builder(make_library())
