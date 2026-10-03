"""The CatalogSource over the catalog models (SPEC §7.5; contract in source.py).

What apps may see:
- categories of the kind with `visible_in_xtream`, allowed by the scope, holding at
  least one ready title (an empty category is a dead end in every app);
- movies with status `ready` in at least one such category;
- series with status `ready` in such a category and with a playable episode; only
  playable episodes are listed (`catalog.services.playable_files`).

A title's `category_ids` are its visible categories in the order they were linked
(the link rows' ids): the library's default categories first, then the genre
categories, so the first is the primary one.

Queries are fixed per call, whatever the catalog size (asserted in the tests):
lists take one query per relation (titles, category links, images, credits, genres),
never one per title.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from django.db.models import Exists, Model, OuterRef, Prefetch, QuerySet, Subquery

from apps.catalog.models import (
    Category,
    Credit,
    CreditRole,
    Episode,
    Genre,
    MediaFile,
    MediaImage,
    Movie,
    Season,
    Series,
    TitleStatus,
)
from apps.catalog.services import media_url, playable_files
from apps.media.models import Rendition, RenditionKind, RenditionStatus
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
from apps.xtream_api.source import CatalogScope

#: Image sizes (MediaImage.sizes keys), best first. Only WebP: apps cannot decode AVIF.
POSTER_SIZES: Final = ("w500", "original", "w185")
POSTER_LARGE_SIZES: Final = ("original", "w500", "w185")
BACKDROP_SIZES: Final = ("w1280", "w780")
STILL_SIZES: Final = ("w500", "w185", "original")
MAX_BACKDROPS: Final = 5
MAX_CAST: Final = 10
CREATOR: Final = "Creator"  # how metadata stores a series' created_by (role writer)

#: A play URL resolves titles that exist and may play soon: anything else is unknown.
_RESOLVABLE: Final = (TitleStatus.READY, TitleStatus.PROCESSING, TitleStatus.REVIEW)
_ART: Final = ("poster", "backdrop")
_NO_VIDEO: Final = VideoStream("", 0, 0)
COMPAT_AUDIO: Final = AudioStream("aac", 2)

MovieLinks = Movie.categories.through
SeriesLinks = Series.categories.through


class _Links:
    """Visible category xc_ids per title, primary first, and adult flags."""

    def __init__(self, rows: Iterable[tuple[UUID, int, bool]]) -> None:
        self.ids: dict[UUID, list[int]] = defaultdict(list)
        self.adult: set[UUID] = set()
        for title_id, category_xc_id, is_adult in rows:
            self.ids[title_id].append(category_xc_id)
            if is_adult:
                self.adult.add(title_id)

    def of(self, title_id: UUID) -> tuple[int, ...]:
        return tuple(self.ids.get(title_id, ()))


class DjangoCatalogSource:
    """CatalogSource over apps.catalog (installed in XtreamApiConfig.ready)."""

    # --- Categories -------------------------------------------------------------------------

    def categories(self, scope: CatalogScope, kind: CategoryKind) -> Sequence[CategoryItem]:
        if kind == "vod" and scope.allow_movies:
            query = _visible_categories(scope, kind).filter(
                Exists(MovieLinks.objects.filter(category=OuterRef("pk"), movie__status="ready"))
            )
        elif kind == "series" and scope.allow_series:
            query = _visible_categories(scope, kind).filter(
                Exists(
                    SeriesLinks.objects.filter(
                        category=OuterRef("pk"), series__in=_playable_series()
                    )
                )
            )
        else:  # live channels arrive in M12
            return ()
        rows = query.order_by("sort", "xc_id").values_list("xc_id", "name_en", "name_ar", "sort")
        return [CategoryItem(xc_id, Text(en, ar), sort) for xc_id, en, ar, sort in rows]

    # --- Movies -----------------------------------------------------------------------------

    def movies(self, scope: CatalogScope) -> Sequence[MovieSummary]:
        if not scope.allow_movies:
            return ()
        links = _movie_links(scope)
        query = (
            _visible_movies(scope)
            .only("id", "xc_id", "title", "title_ar", "created_at", "rating", "tmdb_id")
            .prefetch_related(_images(_ART))
        )
        return [_movie_summary(movie, links) for movie in query]

    def movie(self, scope: CatalogScope, xc_id: int) -> MovieDetail | None:
        if not scope.allow_movies:
            return None
        movie = (
            _visible_movies(scope)
            .filter(xc_id=xc_id)
            .prefetch_related(
                _images(_ART),
                _credits(),
                Prefetch("genres", queryset=Genre.objects.order_by("name_en")),
                _playable_files(),
            )
            .first()
        )
        if movie is None:
            return None
        links = _movie_links(scope, movie_id=movie.pk)
        images = _by_kind(movie)
        credits: list[Credit] = list(movie.credits.all())
        return MovieDetail(
            movie=_movie_summary(movie, links),
            media=_media(_first(movie, "playable"), movie.runtime_min),
            original_title=movie.original_title,
            poster_large=_url(images.get("poster", ()), POSTER_LARGE_SIZES),
            backdrops=_urls(images.get("backdrop", ()), BACKDROP_SIZES),
            overview=Text(movie.overview, movie.overview_ar),
            cast=_names(credits, CreditRole.CAST)[:MAX_CAST],
            directors=_names(credits, CreditRole.DIRECTOR),
            genres=tuple(Text(genre.name_en, genre.name_ar) for genre in movie.genres.all()),
            release_date=movie.release_date,
            year=movie.year,
            countries=tuple(movie.countries),
            imdb_id=movie.imdb_id,
            trailer_key=movie.trailer_youtube_key,
            certification=movie.certification,
        )

    # --- Series -----------------------------------------------------------------------------

    def series_list(self, scope: CatalogScope) -> Sequence[SeriesSummary]:
        if not scope.allow_series:
            return ()
        links = _series_links(scope)
        query = _visible_series(scope).prefetch_related(
            _images(_ART),
            _credits(),
            Prefetch("genres", queryset=Genre.objects.order_by("name_en")),
        )
        return [_series_summary(series, links) for series in query]

    def series(self, scope: CatalogScope, xc_id: int) -> SeriesDetail | None:
        if not scope.allow_series:
            return None
        series = (
            _visible_series(scope)
            .filter(xc_id=xc_id)
            .prefetch_related(
                _images(_ART),
                _credits(),
                Prefetch("genres", queryset=Genre.objects.order_by("name_en")),
                Prefetch(
                    "seasons",
                    queryset=Season.objects.order_by("number").prefetch_related(
                        _images(("poster",))
                    ),
                ),
            )
            .first()
        )
        if series is None:
            return None
        links = _series_links(scope, series_id=series.pk)
        episodes = (
            _playable_episodes()
            .filter(season__series=series)
            .select_related("season")
            .prefetch_related(_playable_files(), _images(("still",)))
        )
        return SeriesDetail(
            series=_series_summary(series, links),
            seasons=tuple(_season(season) for season in series.seasons.all()),
            episodes=tuple(_episode(episode) for episode in episodes),
        )

    def episodes(self, scope: CatalogScope) -> Sequence[EpisodeRef]:
        if not scope.allow_series:
            return ()
        rows = (
            _playable_episodes()
            .filter(season__series__in=_visible_series(scope).values("pk"))
            .values_list("xc_id", "season__series__xc_id", "season__number", "number")
        )
        return [EpisodeRef(xc_id, series, season, number) for xc_id, series, season, number in rows]

    # --- Play URLs --------------------------------------------------------------------------

    def title(self, kind: TitleKind, xc_id: int) -> TitleRef | None:
        """Movies and episodes that exist and are or will soon be playable. Hidden and
        license-expired titles are unknown, so apps get a 404 rather than a retry."""
        if kind is TitleKind.MOVIE:
            pk = (
                Movie.objects.filter(xc_id=xc_id, status__in=_RESOLVABLE)
                .values_list("pk", flat=True)
                .first()
            )
        else:
            pk = (
                Episode.objects.filter(xc_id=xc_id, season__series__status__in=_RESOLVABLE)
                .values_list("pk", flat=True)
                .first()
            )
        return TitleRef(kind, xc_id, str(pk)) if pk is not None else None


# --- Querysets --------------------------------------------------------------------------------


def _visible_categories(scope: CatalogScope, kind: CategoryKind) -> QuerySet[Category]:
    query = Category.objects.filter(kind=kind, visible_in_xtream=True)
    if scope.categories is not None:
        query = query.filter(pk__in=scope.categories)
    return query


def _playable_episodes() -> QuerySet[Episode]:
    return Episode.objects.filter(
        Exists(MediaFile.objects.filter(playable_files(), episodes=OuterRef("pk")))
    )


def _playable_series() -> QuerySet[Series]:
    return Series.objects.filter(status=TitleStatus.READY).filter(
        Exists(_playable_episodes().filter(season__series=OuterRef("pk")))
    )


def _visible_movies(scope: CatalogScope) -> QuerySet[Movie]:
    visible = _visible_categories(scope, "vod")
    return Movie.objects.filter(status=TitleStatus.READY).filter(
        Exists(MovieLinks.objects.filter(movie=OuterRef("pk"), category__in=visible))
    )


def _visible_series(scope: CatalogScope) -> QuerySet[Series]:
    visible = _visible_categories(scope, "series")
    latest = (
        _playable_episodes()
        .filter(season__series=OuterRef("pk"))
        .order_by("-created_at")
        .values("created_at")[:1]
    )
    return (
        _playable_series()
        .filter(Exists(SeriesLinks.objects.filter(series=OuterRef("pk"), category__in=visible)))
        .annotate(latest_episode=Subquery(latest))
    )


def _movie_links(scope: CatalogScope, movie_id: UUID | None = None) -> _Links:
    rows = MovieLinks.objects.filter(category__in=_visible_categories(scope, "vod"))
    rows = rows.filter(movie=movie_id) if movie_id else rows.filter(movie__status="ready")
    return _Links(
        rows.order_by("pk").values_list("movie_id", "category__xc_id", "category__is_adult")
    )


def _series_links(scope: CatalogScope, series_id: UUID | None = None) -> _Links:
    rows = SeriesLinks.objects.filter(category__in=_visible_categories(scope, "series"))
    rows = rows.filter(series=series_id) if series_id else rows.filter(series__status="ready")
    return _Links(
        rows.order_by("pk").values_list("series_id", "category__xc_id", "category__is_adult")
    )


def _images(kinds: Sequence[str]) -> "Prefetch[Any]":
    return Prefetch(
        "images",
        queryset=MediaImage.objects.filter(kind__in=kinds)
        .only("id", "kind", "sizes", "is_primary", "movie_id", "series_id", "season_id",
              "episode_id")
        .order_by("kind", "-is_primary", "created_at"),
    )  # fmt: skip


def _credits() -> "Prefetch[Any]":
    return Prefetch(
        "credits",
        queryset=Credit.objects.filter(
            role__in=(CreditRole.CAST, CreditRole.DIRECTOR, CreditRole.WRITER)
        )
        .select_related("person")
        .order_by("role", "order"),
    )


def _playable_files() -> "Prefetch[Any]":
    """Playable files, primary first, each with its ready compat rendition (`compat`)."""
    compat = Rendition.objects.filter(
        kind=RenditionKind.COMPAT_MP4, status=RenditionStatus.READY
    ).order_by("-height")
    return Prefetch(
        "files",
        queryset=MediaFile.objects.filter(playable_files())
        .distinct()
        .order_by("-is_primary", "created_at")
        .prefetch_related(Prefetch("renditions", queryset=compat, to_attr="compat")),
        to_attr="playable",
    )


# --- Model → DTO ------------------------------------------------------------------------------


def _first(owner: Model, attribute: str) -> Any:
    items: list[Any] = getattr(owner, attribute, [])
    return items[0] if items else None


def _by_kind(owner: Movie | Series | Season | Episode) -> Mapping[str, list[MediaImage]]:
    """Prefetched images by kind, primary first."""
    grouped: dict[str, list[MediaImage]] = defaultdict(list)
    for image in owner.images.all():
        grouped[image.kind].append(image)
    return grouped


def _key(image: MediaImage, sizes: Sequence[str]) -> str:
    stored = image.sizes if isinstance(image.sizes, dict) else {}
    for size in sizes:
        formats = stored.get(size)
        key = formats.get("webp") if isinstance(formats, dict) else None
        if isinstance(key, str) and key:
            return media_url(key)
    return ""


def _url(images: Sequence[MediaImage], sizes: Sequence[str]) -> str:
    return next((url for image in images if (url := _key(image, sizes))), "")


def _urls(images: Sequence[MediaImage], sizes: Sequence[str]) -> tuple[str, ...]:
    urls = [url for image in images if (url := _key(image, sizes))]
    return tuple(urls[:MAX_BACKDROPS])


def _names(credits: Iterable[Credit], role: str, character: str | None = None) -> tuple[str, ...]:
    return tuple(
        credit.person.name
        for credit in credits
        if credit.role == role and (character is None or credit.character == character)
    )


def _media(file: MediaFile | None, runtime_min: int | None = None) -> MediaInfo:
    """What a player receives: the source as probed when it plays directly, else its
    compat rendition (H.264 with AAC stereo first, profiles.yaml); the runtime alone
    when nothing plays yet."""
    fallback_s = (runtime_min or 0) * 60
    if file is None:
        return MediaInfo(fallback_s, 0, _NO_VIDEO)
    compat: Rendition | None = None if file.direct_play else _first(file, "compat")
    if compat is not None:
        return MediaInfo(
            duration_secs=round(compat.duration_s or 0)
            or (file.duration_ms or 0) // 1000
            or fallback_s,
            bitrate_kbps=(compat.bitrate or 0) // 1000,
            video=VideoStream(compat.codec or "h264", compat.width or 0, compat.height or 0),
            audio=COMPAT_AUDIO,
        )
    summary = file.probe_summary if isinstance(file.probe_summary, dict) else {}
    tracks = [track for track in summary.get("audio") or [] if isinstance(track, dict)]
    track = next((t for t in tracks if t.get("default")), tracks[0] if tracks else None)
    audio = (
        AudioStream(str(track.get("codec") or ""), int(track.get("channels") or 0))
        if track
        else AudioStream()
    )
    return MediaInfo(
        duration_secs=(file.duration_ms or 0) // 1000 or fallback_s,
        bitrate_kbps=(file.bitrate or 0) // 1000,
        video=VideoStream(file.video_codec, file.width or 0, file.height or 0),
        audio=audio,
    )


def _movie_summary(movie: Movie, links: _Links) -> MovieSummary:
    return MovieSummary(
        xc_id=movie.xc_id,
        title=Text(movie.title, movie.title_ar),
        added=movie.created_at,
        category_ids=links.of(movie.pk),
        poster=_url(_by_kind(movie).get("poster", ()), POSTER_SIZES),
        rating=movie.rating,
        tmdb_id=movie.tmdb_id,
        is_adult=movie.pk in links.adult,
    )


def _series_summary(series: Series, links: _Links) -> SeriesSummary:
    images = _by_kind(series)
    credits: list[Credit] = list(series.credits.all())
    latest: datetime | None = getattr(series, "latest_episode", None)
    return SeriesSummary(
        xc_id=series.xc_id,
        title=Text(series.title, series.title_ar),
        last_modified=max(latest, series.created_at) if latest else series.created_at,
        category_ids=links.of(series.pk),
        poster=_url(images.get("poster", ()), POSTER_SIZES),
        poster_large=_url(images.get("poster", ()), POSTER_LARGE_SIZES),
        backdrops=_urls(images.get("backdrop", ()), BACKDROP_SIZES),
        overview=Text(series.overview, series.overview_ar),
        cast=_names(credits, CreditRole.CAST)[:MAX_CAST],
        creators=_names(credits, CreditRole.WRITER, CREATOR),
        genres=tuple(Text(genre.name_en, genre.name_ar) for genre in series.genres.all()),
        first_air_date=series.first_air_date,
        rating=series.rating,
        trailer_key=series.trailer_youtube_key,
        episode_run_time=series.episode_run_time,
        tmdb_id=series.tmdb_id,
    )


def _season(season: Season) -> SeasonItem:
    posters = _by_kind(season).get("poster", ())
    return SeasonItem(
        number=season.number,
        id=season.tmdb_id if season.tmdb_id is not None else -1,  # -1: the builder derives one
        name=Text(season.name, season.name_ar),
        poster=_url(posters, POSTER_SIZES),
        poster_large=_url(posters, POSTER_LARGE_SIZES),
        air_date=season.air_date,
        overview=Text(season.overview, season.overview_ar),
    )


def _episode(episode: Episode) -> EpisodeItem:
    return EpisodeItem(
        xc_id=episode.xc_id,
        season=episode.season.number,
        number=episode.number,
        added=episode.created_at,
        media=_media(_first(episode, "playable"), episode.runtime_min),
        title=Text(episode.title, episode.title_ar),
        still=_url(_by_kind(episode).get("still", ()), STILL_SIZES),
        overview=Text(episode.overview, episode.overview_ar),
        air_date=episode.air_date,
        rating=episode.rating,
    )
