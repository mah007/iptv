"""player_api.php payloads with PHP-panel types (SPEC §7.5, compat/schemas).

Pure functions over the records in dto.py: no database, no settings, no clock.
Every type is emitted explicitly (`str(xc_id)`, `value or ""`); nothing relies on
a serializer's defaults. The traps are in .claude/skills/xtream-contract/SKILL.md:
strings for category ids, timestamps, counts and ports; ints for stream, series
and season numbers; "" instead of null; `backdrop_path` always a list; `episodes`
an object keyed by season-number strings.

Lists follow category order (`sort`, then id), then newest first. An item keeps
only the categories visible to the user; one left with none is not listed.
"""

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, Protocol

from apps.xtream_api.dto import (
    CategoryItem,
    EpisodeItem,
    Locale,
    MediaInfo,
    MovieDetail,
    MovieSummary,
    SeasonItem,
    SeriesDetail,
    SeriesSummary,
    Text,
)

type Payload = dict[str, object]
type AccountStatus = Literal["Active", "Expired", "Disabled"]

#: The failed-login body: HTTP 200, the same bytes for an unknown user and a wrong password.
AUTH_FAILURE = b'{"user_info":{"auth":0}}'
EMPTY_LIST = b"[]"
EMPTY_EPG = b'{"epg_listings":[]}'
#: get_vod_info / get_series_info for an unknown or hidden id (sent with HTTP 404).
NOT_FOUND = b"{}"

CONTAINER_EXTENSION = "mp4"  # apps always play the compatible MP4 rendition
ALLOWED_OUTPUT_FORMATS = ("m3u8", "ts", "mp4")
#: exp_date of an access profile without an end (schema: never null): 2100-01-01 UTC.
NO_END_TIMESTAMP = 4_102_444_800
MAX_SEASON = 9999  # season keys have at most four digits
_MAX_UNIX = 9_999_999_999
_MAX_COUNT = 999_999
_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

_IMAGE_URL = re.compile(r"https?://[^\s\"<>]+\.(?:jpe?g|png|webp)(?:\?[^\s\"<>]*)?")
_YOUTUBE_KEY = re.compile(r"[A-Za-z0-9_-]{11}")
_IMDB_ID = re.compile(r"tt[0-9]{7,10}")

_SEASON_NAMES: dict[Locale, tuple[str, str]] = {
    "en": ("Season {number}", "Specials"),
    "ar": ("الموسم {number}", "حلقات خاصة"),
}
_EPISODE_NAMES: dict[Locale, str] = {"en": "Episode {number}", "ar": "الحلقة {number}"}


# --- Scalars ----------------------------------------------------------------------------


def unix(moment: datetime) -> str:
    return str(min(max(int(moment.timestamp()), 0), _MAX_UNIX))


def image(url: str) -> str:
    """An absolute JPEG, PNG or WebP URL, or "" (TV apps cannot decode AVIF or SVG)."""
    return url if _IMAGE_URL.fullmatch(url) else ""


def images(urls: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(url for url in map(image, urls) if url))


def rating(value: float | None) -> tuple[str, float | int]:
    """("8.2", 4.1): the 0-10 rating as a string with one decimal and rating / 2.

    0, missing or out-of-range ratings are unrated: ("", 0).
    """
    if value is None or not 0 < value <= 10:
        return "", 0
    rounded = round(value, 1)
    return f"{rounded:.1f}", round(rounded / 2, 1)


def tmdb(value: int | None) -> str:
    return str(value) if value is not None and 0 < value <= _MAX_UNIX else ""


def iso_date(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def year(release_date: date | None, value: int | None = None) -> str:
    candidate = value if value is not None else (release_date.year if release_date else None)
    return str(candidate) if candidate is not None and 1000 <= candidate <= 2999 else ""


def minutes(value: int | None) -> str:
    return str(value) if value is not None and 0 < value < 10_000 else ""


def duration(seconds: int) -> str:
    """HH:MM:SS (at most 999 hours)."""
    total = min(max(seconds, 0), 999 * 3600 + 3599)
    hours, rest = divmod(total, 3600)
    return f"{hours:02d}:{rest // 60:02d}:{rest % 60:02d}"


def joined(values: Iterable[str]) -> str:
    return ", ".join(value.strip() for value in values if value.strip())


def name(text: Text, locale: Locale, fallback: str) -> str:
    return text.pick(locale) or fallback


def _matching(pattern: re.Pattern[str], value: str) -> str:
    return value if pattern.fullmatch(value) else ""


def _media(media: MediaInfo) -> Payload:
    return {
        "duration_secs": max(media.duration_secs, 0),
        "duration": duration(media.duration_secs),
        "video": {
            "codec_name": media.video.codec,
            "width": media.video.width,
            "height": media.video.height,
        },
        "audio": {"codec_name": media.audio.codec, "channels": max(media.audio.channels, 0)},
        "bitrate": max(media.bitrate_kbps, 0),
    }


# --- Login --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """Where IPTV apps reach this server; they build every URL from it."""

    host: str
    scheme: Literal["http", "https"]
    http_port: int
    https_port: int

    @property
    def origin(self) -> str:
        port = self.https_port if self.scheme == "https" else self.http_port
        default = 443 if self.scheme == "https" else 80
        suffix = "" if port == default else f":{port}"
        return f"{self.scheme}://{self.host}{suffix}"


@dataclass(frozen=True, slots=True)
class AccountInfo:
    username: str  # echoed as sent
    password: str  # echoed as sent; apps expect it back
    status: AccountStatus
    expires_at: datetime | None  # None: no end
    created_at: datetime
    max_connections: int
    active_connections: int
    is_trial: bool = False


def login(account: AccountInfo, server: ServerInfo, now: datetime) -> Payload:
    timestamp = int(now.timestamp())
    expires = unix(account.expires_at) if account.expires_at else str(NO_END_TIMESTAMP)
    return {
        "user_info": {
            "username": account.username,
            "password": account.password,
            "message": "",
            "auth": 1,
            "status": account.status,
            "exp_date": expires,
            "is_trial": "1" if account.is_trial else "0",
            "active_cons": str(min(max(account.active_connections, 0), _MAX_COUNT)),
            "created_at": unix(account.created_at),
            "max_connections": str(min(max(account.max_connections, 1), _MAX_COUNT)),
            "allowed_output_formats": list(ALLOWED_OUTPUT_FORMATS),
        },
        "server_info": {
            "url": server.host,
            "port": str(server.http_port),
            "https_port": str(server.https_port),
            "server_protocol": server.scheme,
            "rtmp_port": "0",
            "timezone": "UTC",
            "timestamp_now": timestamp,
            "time_now": datetime.fromtimestamp(timestamp, UTC).strftime(_DATETIME_FORMAT),
            "process": True,
        },
    }


# --- Categories -----------------------------------------------------------------------------


def ordered_categories(categories: Iterable[CategoryItem]) -> list[CategoryItem]:
    unique = {category.xc_id: category for category in categories if category.xc_id > 0}
    return sorted(unique.values(), key=lambda category: (category.sort, category.xc_id))


def categories(items: Iterable[CategoryItem], locale: Locale) -> list[Payload]:
    return [
        {
            "category_id": str(category.xc_id),
            "category_name": name(category.name, locale, str(category.xc_id)),
            "parent_id": 0,
        }
        for category in ordered_categories(items)
    ]


class _Categorised(Protocol):
    @property
    def xc_id(self) -> int: ...

    @property
    def category_ids(self) -> tuple[int, ...]: ...


def visible[T: _Categorised](
    items: Iterable[T],
    categories: Iterable[CategoryItem],
    newest: Callable[[T], datetime],
    category_id: int | None = None,
) -> list[tuple[T, list[int]]]:
    """Items with their visible category ids (primary first), in list order.

    Optionally only those in `category_id`. Order: primary category's position,
    newest first, then id. Duplicate ids are listed once.
    """
    position = {
        category.xc_id: index for index, category in enumerate(ordered_categories(categories))
    }
    rows: dict[int, tuple[T, list[int]]] = {}
    for item in items:
        ids = [cid for cid in dict.fromkeys(item.category_ids) if cid in position]
        if not ids or item.xc_id in rows or (category_id is not None and category_id not in ids):
            continue
        rows[item.xc_id] = (item, ids)
    return sorted(
        rows.values(),
        key=lambda row: (position[row[1][0]], -newest(row[0]).timestamp(), row[0].xc_id),
    )


# --- Movies -----------------------------------------------------------------------------------


def _added(movie: MovieSummary) -> datetime:
    return movie.added


def vod_streams(
    movies: Iterable[MovieSummary],
    categories: Iterable[CategoryItem],
    locale: Locale,
    category_id: int | None = None,
) -> list[Payload]:
    payload: list[Payload] = []
    for number, (movie, ids) in enumerate(visible(movies, categories, _added, category_id), 1):
        rating_text, rating_5 = rating(movie.rating)
        payload.append(
            {
                "num": number,
                "name": name(movie.title, locale, str(movie.xc_id)),
                "stream_type": "movie",
                "stream_id": movie.xc_id,
                "stream_icon": image(movie.poster),
                "rating": rating_text,
                "rating_5based": rating_5,
                "added": unix(movie.added),
                "category_id": str(ids[0]),
                "category_ids": ids,
                "container_extension": CONTAINER_EXTENSION,
                "custom_sid": "",
                "direct_source": "",
                "tmdb_id": tmdb(movie.tmdb_id),
                "is_adult": "1" if movie.is_adult else "0",
            }
        )
    return payload


def vod_info(
    detail: MovieDetail, categories: Iterable[CategoryItem], locale: Locale
) -> Payload | None:
    """get_vod_info, with every info field repeated at the root; None if not visible."""
    rows = visible((detail.movie,), categories, _added)
    if not rows:
        return None
    movie, ids = rows[0]
    title = name(movie.title, locale, str(movie.xc_id))
    plot = detail.overview.pick(locale)
    cast = joined(detail.cast)
    rating_text, rating_5 = rating(movie.rating)
    media = _media(detail.media)
    info: Payload = {
        "name": title,
        "o_name": detail.original_title.strip(),
        "cover_big": image(detail.poster_large) or image(movie.poster),
        "movie_image": image(movie.poster),
        "backdrop_path": images(detail.backdrops),
        "plot": plot,
        "description": plot,
        "cast": cast,
        "actors": cast,
        "director": joined(detail.directors),
        "genre": joined(genre.pick(locale) for genre in detail.genres),
        "release_date": iso_date(detail.release_date),
        "releasedate": iso_date(detail.release_date),
        "year": year(detail.release_date, detail.year),
        "country": joined(detail.countries),
        "rating": rating_text,
        "rating_5based": rating_5,
        "duration_secs": media["duration_secs"],
        "duration": media["duration"],
        "tmdb_id": tmdb(movie.tmdb_id),
        "imdb_id": _matching(_IMDB_ID, detail.imdb_id),
        "youtube_trailer": _matching(_YOUTUBE_KEY, detail.trailer_key),
        "age": detail.certification.strip(),
        "video": media["video"],
        "audio": media["audio"],
        "bitrate": media["bitrate"],
    }
    movie_data: Payload = {
        "stream_id": movie.xc_id,
        "name": title,
        "added": unix(movie.added),
        "category_id": str(ids[0]),
        "container_extension": CONTAINER_EXTENSION,
        "custom_sid": "",
        "direct_source": "",
    }
    return {"info": info, "movie_data": movie_data, **info}


# --- Series -----------------------------------------------------------------------------------


def _modified(series: SeriesSummary) -> datetime:
    return series.last_modified


def _series_fields(series: SeriesSummary, ids: list[int], locale: Locale) -> Payload:
    first_aired = iso_date(series.first_air_date)
    rating_text, rating_5 = rating(series.rating)
    return {
        "name": name(series.title, locale, str(series.xc_id)),
        "cover": image(series.poster),
        "plot": series.overview.pick(locale),
        "cast": joined(series.cast),
        "director": joined(series.creators),
        "genre": joined(genre.pick(locale) for genre in series.genres),
        "releaseDate": first_aired,
        "release_date": first_aired,
        "last_modified": unix(series.last_modified),
        "rating": rating_text,
        "rating_5based": rating_5,
        "backdrop_path": images(series.backdrops),
        "youtube_trailer": _matching(_YOUTUBE_KEY, series.trailer_key),
        "episode_run_time": minutes(series.episode_run_time),
        "category_id": str(ids[0]),
        "category_ids": ids,
        "tmdb": tmdb(series.tmdb_id),
    }


def series_list(
    series: Iterable[SeriesSummary],
    categories: Iterable[CategoryItem],
    locale: Locale,
    category_id: int | None = None,
) -> list[Payload]:
    payload: list[Payload] = []
    for number, (item, ids) in enumerate(visible(series, categories, _modified, category_id), 1):
        fields = _series_fields(item, ids, locale)
        payload.append(
            {"num": number, "name": fields.pop("name"), "series_id": item.xc_id, **fields}
        )
    return payload


class _Numbered(Protocol):
    @property
    def xc_id(self) -> int: ...

    @property
    def season(self) -> int: ...

    @property
    def number(self) -> int: ...


def playable_seasons[E: _Numbered](episodes: Iterable[E]) -> dict[int, list[E]]:
    """Episodes by season in numeric order; one per (season, number), lowest id first.

    Seasons outside 0-9999 and negative episode numbers cannot be expressed in the
    Xtream shape and are left out. get_series_info and the M3U share this rule.
    """
    seasons: dict[int, dict[int, E]] = {}
    for episode in sorted(episodes, key=lambda item: (item.season, item.number, item.xc_id)):
        if 0 <= episode.season <= MAX_SEASON and episode.number >= 0 and episode.xc_id > 0:
            seasons.setdefault(episode.season, {}).setdefault(episode.number, episode)
    return {season: list(by_number.values()) for season, by_number in seasons.items()}


def series_info(
    detail: SeriesDetail, categories: Iterable[CategoryItem], locale: Locale
) -> Payload | None:
    """get_series_info; None if the series is not visible."""
    rows = visible((detail.series,), categories, _modified)
    if not rows:
        return None
    series, ids = rows[0]
    known = {season.number: season for season in detail.seasons}
    episodes: dict[str, list[Payload]] = {}
    seasons: list[Payload] = []
    for number, items in playable_seasons(detail.episodes).items():
        episodes[str(number)] = [_episode(item, locale) for item in items]
        seasons.append(_season(number, known.get(number), len(items), series, locale))
    return {
        "seasons": seasons,
        "info": _series_fields(series, ids, locale),
        "episodes": episodes,
    }


def _season(
    number: int, season: SeasonItem | None, count: int, series: SeriesSummary, locale: Locale
) -> Payload:
    regular, specials = _SEASON_NAMES[locale]
    fallback = specials if number == 0 else regular.format(number=number)
    poster = image(season.poster) if season else ""
    poster_large = image(season.poster_large) if season else ""
    return {
        "season_number": number,
        # A generated "Season 2" in the user's language beats a name in the other one.
        "name": (season.name.only(locale) if season else "") or fallback,
        "episode_count": count,
        "cover": poster or image(series.poster),
        "cover_big": poster_large or poster or image(series.poster_large) or image(series.poster),
        "air_date": iso_date(season.air_date) if season else "",
        "overview": season.overview.pick(locale) if season else "",
        # Stable without TMDB: unique per series and season.
        "id": season.id if season and season.id >= 0 else series.xc_id * 10_000 + number,
    }


def _episode(episode: EpisodeItem, locale: Locale) -> Payload:
    aired = iso_date(episode.air_date)
    rating_text, _ = rating(episode.rating)
    fallback = _EPISODE_NAMES[locale].format(number=episode.number)
    media = _media(episode.media)
    return {
        "id": str(episode.xc_id),
        "episode_num": episode.number,
        "title": name(episode.title, locale, fallback),
        "container_extension": CONTAINER_EXTENSION,
        "season": episode.season,
        "added": unix(episode.added),
        "custom_sid": "",
        "direct_source": "",
        "info": {
            "movie_image": image(episode.still),
            "plot": episode.overview.pick(locale),
            "duration_secs": media["duration_secs"],
            "duration": media["duration"],
            "rating": rating_text,
            "air_date": aired,
            "releasedate": aired,
            "video": media["video"],
            "audio": media["audio"],
            "bitrate": media["bitrate"],
        },
    }


def ids_of(categories: Sequence[Payload]) -> set[int]:
    """The category ids a categories payload lists."""
    return {int(str(category["category_id"])) for category in categories}
