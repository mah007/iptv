"""A realistic catalog in the real models, for the Django catalog source and play tests.

Categories: movies Action and Drama, a category hidden from Xtream, an empty one;
series Crime and Documentaries. `add_movies(n)` and `add_series(n)` grow it with
fully enriched titles (images, credits, genres, probed files), so query counts can
be compared at two sizes.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from itertools import count
from typing import Any

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
from apps.library.models import Library
from apps.media.models import Rendition, RenditionKind, RenditionStatus

PROBE = {
    "audio": [
        {"stream_index": 1, "codec": "eac3", "channels": 6, "language": "eng", "default": False},
        {"stream_index": 2, "codec": "ac3", "channels": 6, "language": "ara", "default": True},
    ],
    "subtitles": [],
}


def _sizes(kind: str, owner: str, sizes: tuple[str, ...]) -> dict[str, dict[str, str]]:
    return {
        size: {
            "webp": f"images/{owner}/{kind}/{size}.abc.webp",
            "avif": f"images/{owner}/{kind}/{size}.abc.avif",
        }
        for size in sizes
    }


@dataclass
class World:
    library: Library
    action: Category
    drama: Category
    hidden: Category
    empty: Category
    crime: Category
    docs: Category
    genres: list[Genre]
    numbers: Any = field(default_factory=lambda: count(1))

    def file(self, *, direct: bool = True, compat: bool = False, **links: Any) -> MediaFile:
        number = next(self.numbers)
        media_file = MediaFile.objects.create(
            library=self.library,
            storage_key=f"title-{number}.mkv",
            state=FileState.MATCHED,
            container="mkv",
            duration_ms=7_260_500,
            bitrate=8_000_000,
            video_codec="hevc",
            width=3840,
            height=2160,
            probe_summary=PROBE,
            direct_play=direct,
            **links,
        )
        if compat:
            Rendition.objects.create(
                media_file=media_file,
                kind=RenditionKind.COMPAT_MP4,
                storage_key=media_file.pk.hex,
                container="mp4",
                width=1920,
                height=1080,
                bitrate=5_000_000,
                codec="h264",
                status=RenditionStatus.READY,
                duration_s=7260.4,
            )
        return media_file

    def image(self, kind: str, sizes: tuple[str, ...], **owner: Any) -> MediaImage:
        label = next(iter(owner.values())).pk
        return MediaImage.objects.create(
            kind=kind, is_primary=True, sizes=_sizes(kind, str(label), sizes), **owner
        )

    def credits(self, **owner: Any) -> None:
        for order, (role, character) in enumerate(
            [
                (CreditRole.CAST, "Neo"),
                (CreditRole.CAST, "Trinity"),
                (CreditRole.DIRECTOR, ""),
                (CreditRole.WRITER, "Creator"),
                (CreditRole.WRITER, "Screenplay"),
            ]
        ):
            person = Person.objects.create(name=f"Person {next(self.numbers)}")
            Credit.objects.create(
                person=person, role=role, character=character, order=order, **owner
            )

    def add_movies(
        self, n: int, *, status: str = TitleStatus.READY, compat: bool = False
    ) -> list[Movie]:
        movies = []
        for _ in range(n):
            number = next(self.numbers)
            movie = Movie.objects.create(
                title=f"Movie {number}",
                title_ar=f"فيلم {number}",
                overview="An overview.",
                overview_ar="نبذة.",
                year=1999,
                release_date=date(1999, 3, 31),
                rating=8.27,
                tmdb_id=10_000 + number,
                imdb_id="tt0133093",
                trailer_youtube_key="vKQi3bBA1y8",
                certification="R",
                countries=["US"],
                runtime_min=136,
                status=status,
            )
            movie.categories.add(self.drama)
            movie.categories.add(self.action, self.hidden)
            movie.genres.set(self.genres)
            self.image("poster", ("w185", "w500", "original"), movie=movie)
            self.image("backdrop", ("w780", "w1280"), movie=movie)
            self.credits(movie=movie)
            self.file(movie=movie, direct=not compat, compat=compat)
            movies.append(movie)
        return movies

    def add_series(self, n: int) -> list[Series]:
        shows = []
        for _ in range(n):
            number = next(self.numbers)
            show = Series.objects.create(
                title=f"Series {number}",
                title_ar=f"مسلسل {number}",
                overview="A series.",
                first_air_date=date(2008, 1, 20),
                rating=9.0,
                tmdb_id=20_000 + number,
                episode_run_time=47,
                status=TitleStatus.READY,
            )
            show.categories.add(self.crime, self.docs)
            show.genres.set(self.genres)
            self.image("poster", ("w185", "w500", "original"), series=show)
            self.image("backdrop", ("w1280",), series=show)
            self.credits(series=show)
            specials = Season.objects.create(series=show, number=0, name="Specials")
            first = Season.objects.create(
                series=show, number=1, tmdb_id=3572 + number, name="Season 1", name_ar="الموسم 1"
            )
            Season.objects.create(series=show, number=2)  # announced, no files yet
            self.image("poster", ("w500",), season=first)
            for season, episode_number, playable in (
                (first, 1, True),
                (first, 2, True),
                (first, 3, False),  # still being prepared
                (specials, 1, True),
            ):
                episode = Episode.objects.create(
                    season=season,
                    number=episode_number,
                    title=f"Episode {episode_number}",
                    runtime_min=47,
                )
                media_file = self.file(direct=False, compat=playable)
                media_file.episodes.add(episode)
                self.image("still", ("w185", "w500"), episode=episode)
            shows.append(show)
        return shows


def build_world(make_library: Callable[..., Library]) -> World:
    def category(kind: str, slug: str, sort: int, **extra: Any) -> Category:
        return Category.objects.create(
            kind=kind, slug=slug, name_en=slug.title(), name_ar=f"ar-{slug}", sort=sort, **extra
        )

    return World(
        library=make_library(),
        action=category(CategoryKind.VOD, "action", 1),
        drama=category(CategoryKind.VOD, "drama", 2),
        hidden=category(CategoryKind.VOD, "hidden", 0, visible_in_xtream=False),
        empty=category(CategoryKind.VOD, "empty", 3),
        crime=category(CategoryKind.SERIES, "crime", 2),
        docs=category(CategoryKind.SERIES, "documentaries", 1, is_adult=True),
        genres=[
            Genre.objects.create(tmdb_id=28, name_en="Action", name_ar="أكشن"),
            Genre.objects.create(tmdb_id=18, name_en="Drama", name_ar="دراما"),
        ],
    )
