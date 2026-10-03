"""Test doubles: an in-memory catalog source, a playback starter, and the sample
catalog of the golden fixtures in compat/fixtures (The Matrix, an Arabic
documentary, Breaking Bad with a special)."""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime

from apps.xtream_api.dto import (
    AudioStream,
    CategoryItem,
    CategoryKind,
    EpisodeItem,
    EpisodeRef,
    MediaInfo,
    MovieDetail,
    MovieSummary,
    SeasonItem,
    SeriesDetail,
    SeriesSummary,
    Text,
    TitleKind,
    TitleRef,
    VideoStream,
)
from apps.xtream_api.playback import PlayOutcome, PlayRequest, PlayStarted
from apps.xtream_api.source import CatalogScope

IMAGES = "https://media.example.com/images"
EDGE_URL = "https://media.example.com/v/c2lnbmVkLXRva2Vu/compat.mp4"


def at(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, UTC)


@dataclass(frozen=True)
class FakeCategory:
    pk: str  # what entitlements list (Category primary keys)
    kind: CategoryKind
    item: CategoryItem


@dataclass
class FakeCatalog:
    categories: list[FakeCategory]
    movies: list[MovieDetail]
    series: list[SeriesDetail]


class InMemoryCatalogSource:
    """CatalogSource over a FakeCatalog, honouring the scope like the real one must."""

    def __init__(self, catalog: FakeCatalog) -> None:
        self.catalog = catalog
        self.calls: Counter[str] = Counter()

    def _allowed(self, scope: CatalogScope, kind: CategoryKind) -> list[CategoryItem]:
        return [
            category.item
            for category in self.catalog.categories
            if category.kind == kind
            and (scope.categories is None or category.pk in scope.categories)
        ]

    def _visible(
        self, scope: CatalogScope, kind: CategoryKind, ids: Sequence[int]
    ) -> tuple[int, ...]:
        allowed = {category.xc_id for category in self._allowed(scope, kind)}
        return tuple(cid for cid in ids if cid in allowed)

    def categories(self, scope: CatalogScope, kind: CategoryKind) -> Sequence[CategoryItem]:
        self.calls["categories"] += 1
        return self._allowed(scope, kind)

    def _movies(self, scope: CatalogScope) -> list[MovieDetail]:
        if not scope.allow_movies:
            return []
        result = []
        for detail in self.catalog.movies:
            ids = self._visible(scope, "vod", detail.movie.category_ids)
            if ids:
                result.append(replace(detail, movie=replace(detail.movie, category_ids=ids)))
        return result

    def movies(self, scope: CatalogScope) -> Sequence[MovieSummary]:
        self.calls["movies"] += 1
        return [detail.movie for detail in self._movies(scope)]

    def movie(self, scope: CatalogScope, xc_id: int) -> MovieDetail | None:
        self.calls["movie"] += 1
        return next((d for d in self._movies(scope) if d.movie.xc_id == xc_id), None)

    def _series(self, scope: CatalogScope) -> list[SeriesDetail]:
        if not scope.allow_series:
            return []
        result = []
        for detail in self.catalog.series:
            ids = self._visible(scope, "series", detail.series.category_ids)
            if ids and detail.episodes:
                result.append(replace(detail, series=replace(detail.series, category_ids=ids)))
        return result

    def series_list(self, scope: CatalogScope) -> Sequence[SeriesSummary]:
        self.calls["series_list"] += 1
        return [detail.series for detail in self._series(scope)]

    def series(self, scope: CatalogScope, xc_id: int) -> SeriesDetail | None:
        self.calls["series"] += 1
        return next((d for d in self._series(scope) if d.series.xc_id == xc_id), None)

    def episodes(self, scope: CatalogScope) -> Sequence[EpisodeRef]:
        self.calls["episodes"] += 1
        return [
            EpisodeRef(e.xc_id, detail.series.xc_id, e.season, e.number)
            for detail in self._series(scope)
            for e in detail.episodes
        ]

    def title(self, kind: TitleKind, xc_id: int) -> TitleRef | None:
        self.calls["title"] += 1
        if kind is TitleKind.MOVIE:
            ids = {detail.movie.xc_id for detail in self.catalog.movies}
        else:
            ids = {e.xc_id for detail in self.catalog.series for e in detail.episodes}
        return TitleRef(kind, xc_id, f"{kind.value}-{xc_id}") if xc_id in ids else None


@dataclass
class FakeStarter:
    """PlaybackStarter that records requests and answers `outcome` (or raises `error`)."""

    outcome: PlayOutcome = field(default_factory=lambda: PlayStarted(EDGE_URL))
    error: Exception | None = None
    active: int = 0
    requests: list[PlayRequest] = field(default_factory=list)

    def start(self, request: PlayRequest) -> PlayOutcome:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.outcome

    def active_streams(self, user_id: str) -> int:
        return self.active


# --- The sample catalog (compat/fixtures) -----------------------------------------------

H264_1080 = VideoStream("h264", 1920, 800)
H264_720 = VideoStream("h264", 1280, 720)
AAC_STEREO = AudioStream("aac", 2)

ACTION = CategoryItem(11, Text("Action", "أكشن"), sort=1)
SCI_FI = CategoryItem(12, Text("Science Fiction", "خيال علمي"), sort=2)
DOCUMENTARIES = CategoryItem(14, Text("Documentaries", "وثائقيات"), sort=4)
DRAMA = CategoryItem(21, Text("Drama", "دراما"), sort=1)
CRIME = CategoryItem(22, Text("Crime", "جريمة"), sort=2)

MATRIX = MovieDetail(
    movie=MovieSummary(
        xc_id=1001,
        title=Text("The Matrix", "ماتريكس"),
        added=at(1791032700),
        category_ids=(12, 11),
        poster=f"{IMAGES}/movie-1001/poster/w500.5d41402a.webp",
        rating=8.2,
        tmdb_id=603,
    ),
    media=MediaInfo(8160, 4573, H264_1080, AAC_STEREO),
    original_title="The Matrix",
    poster_large=f"{IMAGES}/movie-1001/poster/original.5d41402a.webp",
    backdrops=(f"{IMAGES}/movie-1001/backdrop/w1280.7b52009b.webp",),
    overview=Text(
        "A computer hacker learns that the world he lives in is a simulation and joins the "
        "rebels fighting its machine rulers.",
        "يكتشف مخترق حواسيب أن العالم الذي يعيش فيه محاكاة، فينضم إلى المتمردين.",
    ),
    cast=(
        "Keanu Reeves",
        "Laurence Fishburne",
        "Carrie-Anne Moss",
        "Hugo Weaving",
        "Joe Pantoliano",
    ),
    directors=("Lana Wachowski", "Lilly Wachowski"),
    genres=(ACTION.name, SCI_FI.name),
    release_date=date(1999, 3, 30),
    countries=("United States",),
    imdb_id="tt0133093",
    trailer_key="vKQi3bBA1y8",
    certification="R",
)

ALULA = MovieDetail(
    movie=MovieSummary(
        xc_id=1002,
        title=Text("", "رحلة إلى العُلا"),  # Arabic only: English users see it too
        added=at(1791033600),
        category_ids=(14,),
        poster=f"{IMAGES}/movie-1002/poster/w500.c4ca4238.webp",
    ),
    media=MediaInfo(3120, 3100, H264_720, AAC_STEREO),
    original_title="رحلة إلى العُلا",
    genres=(DOCUMENTARIES.name,),
)

BREAKING_BAD = SeriesDetail(
    series=SeriesSummary(
        xc_id=2001,
        title=Text("Breaking Bad", "بريكنج باد"),
        last_modified=at(1791034800),
        category_ids=(21, 22),
        poster=f"{IMAGES}/series-2001/poster/w500.a87ff679.webp",
        backdrops=(f"{IMAGES}/series-2001/backdrop/w1280.e4da3b7f.webp",),
        overview=Text(
            "A chemistry teacher diagnosed with terminal cancer starts making methamphetamine "
            "to secure his family's future."
        ),
        cast=("Bryan Cranston", "Aaron Paul", "Anna Gunn", "RJ Mitte", "Dean Norris"),
        creators=("Vince Gilligan",),
        genres=(DRAMA.name, CRIME.name),
        first_air_date=date(2008, 1, 20),
        rating=8.9,
        episode_run_time=45,
        tmdb_id=1396,
    ),
    seasons=(
        SeasonItem(
            number=0,
            id=3577,
            name=Text("Specials", "حلقات خاصة"),
            poster=f"{IMAGES}/series-2001-s0/poster/w500.1679091c.webp",
            poster_large=f"{IMAGES}/series-2001-s0/poster/original.1679091c.webp",
            air_date=date(2009, 2, 17),
        ),
        SeasonItem(
            number=1,
            id=3572,
            name=Text("Season 1"),
            poster=f"{IMAGES}/series-2001-s1/poster/w500.8f14e45f.webp",
            poster_large=f"{IMAGES}/series-2001-s1/poster/original.8f14e45f.webp",
            air_date=date(2008, 1, 20),
            overview=Text(
                "A high school chemistry teacher's life changes after a terminal diagnosis."
            ),
        ),
        SeasonItem(number=2, id=3573, name=Text("Season 2")),  # no playable episode yet
    ),
    episodes=(
        EpisodeItem(
            xc_id=5001,
            season=1,
            number=1,
            added=at(1791034200),
            media=MediaInfo(3480, 2650, H264_720, AAC_STEREO),
            title=Text("Pilot"),
            still=f"{IMAGES}/episode-5001/still/w500.c9f0f895.webp",
            overview=Text(
                "A chemistry teacher learns he has terminal cancer and makes a dangerous decision."
            ),
            air_date=date(2008, 1, 20),
            rating=8.3,
        ),
        EpisodeItem(
            xc_id=5002,
            season=1,
            number=2,
            added=at(1791034500),
            media=MediaInfo(2880, 2590, H264_720, AAC_STEREO),
            title=Text("Cat's in the Bag..."),
            still=f"{IMAGES}/episode-5002/still/w500.d3d94468.webp",
            overview=Text("Walt and Jesse have to deal with the aftermath of their first cook."),
            air_date=date(2008, 1, 27),
            rating=8.1,
        ),
        EpisodeItem(
            xc_id=5003,
            season=0,
            number=1,
            added=at(1791034680),
            media=MediaInfo(180, 2410, H264_720, AAC_STEREO),
            title=Text("Good Cop / Bad Cop"),
            still=f"{IMAGES}/episode-5003/still/w500.45c48cce.webp",
            air_date=date(2009, 2, 17),
            rating=7.1,
        ),
    ),
)

VOD_CATEGORIES = (ACTION, SCI_FI, DOCUMENTARIES)
SERIES_CATEGORIES = (DRAMA, CRIME)


def sample_catalog(pks: Mapping[int, str] | None = None) -> FakeCatalog:
    """The fixtures' catalog; `pks` maps category xc_ids to entitlement category keys."""
    keys = pks or {}

    def category(kind: CategoryKind, item: CategoryItem) -> FakeCategory:
        return FakeCategory(keys.get(item.xc_id, f"category-{item.xc_id}"), kind, item)

    return FakeCatalog(
        categories=[
            *(category("vod", item) for item in VOD_CATEGORIES),
            *(category("series", item) for item in SERIES_CATEGORIES),
        ],
        movies=[MATRIX, ALULA],
        series=[BREAKING_BAD],
    )
