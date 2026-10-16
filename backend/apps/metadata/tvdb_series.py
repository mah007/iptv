"""TheTVDB as the TV fallback (SPEC §1.3, §7.2; ADR-0023).

TMDB stays the primary provider. TheTVDB is asked only for series, and only when a key
is set (Settings → Integrations → TheTVDB) and TMDB had no confident answer:

- a `[tvdbid-N]` tag TMDB's `find` doesn't know: TheTVDB's series N, confidence 1.0;
- a title TMDB found nothing for, or nothing above the threshold (`no_candidates`,
  `below_threshold`): TheTVDB's search results are scored with the same scorer and the
  same thresholds. An `ambiguous` TMDB answer goes to the review queue as before.

A confident TheTVDB match is placed, in order:

1. on a series we already hold with that TheTVDB id;
2. through TMDB, when TheTVDB names the series' TMDB id (the cross-walk): TMDB stays
   the source of the metadata, and only the title search had failed;
3. on a series built from TheTVDB itself (`metadata_source=tvdb`): English and Arabic
   names and overviews from its translations (Arabic left empty when there is none),
   seasons and episodes in the series' ordering (`episode_ordering`: aired, the default,
   or DVD), genres mapped onto TMDB's TV genres, cast and directors, poster, background,
   logo, season posters and episode stills.

Anything undecided becomes one review with the top five of both providers, each
candidate naming its provider. TheTVDB failures never stop matching: they are logged
and the file goes to the review queue with TMDB's candidates.

TheTVDB's licence asks for attribution wherever its data shows ("Metadata provided by
TheTVDB", with a link): the apps show it on series whose source is `tvdb` and on the
About pages. Above USD 50,000 of yearly company revenue its API needs a paid licence.
"""

import logging
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from django.db import transaction
from redis.exceptions import LockError

from apps.accounts.models import User
from apps.catalog.models import (
    Credit,
    CreditRole,
    Episode,
    EpisodeOrdering,
    ImageKind,
    ImageSource,
    MediaFile,
    MetadataSource,
    Person,
    ReviewKind,
    Season,
    Series,
)
from apps.catalog.services import categories_for_genres
from apps.catalog.signals import notify_catalog_changed
from apps.core.errors import ErrorCode, ProblemError
from apps.core.metrics import MATCH_CONFIDENCE
from apps.core.services import get_setting
from apps.core.stores import state_redis
from apps.library.parsing import ParseResult
from apps.metadata import services
from apps.metadata.scoring import Candidate, MatchDecision, MatchQuery, ScoredCandidate, decide
from apps.metadata.services import (
    ImagePick,
    PlacementError,
    _arabic,
    _assign,
    _date,
    _genres,
    _int,
    _positive,
    _save_title,
    _str,
    _thresholds,
)
from apps.metadata.tmdb import TMDBClient, TMDBNotFoundError
from apps.metadata.tvdb import SeasonType, TVDBClient, TVDBError, TVDBNotFoundError
from apps.metadata.tvdb.factory import client_for, tvdb_config

logger = logging.getLogger(__name__)

#: TMDB decisions that make TheTVDB worth asking (an `ambiguous` one has candidates).
FALLBACK_REASONS: Final = frozenset({"no_candidates", "below_threshold"})
SEARCH_LIMIT: Final = 10
CAST_LIMIT: Final = 15
REVIEW_LIMIT: Final = 5
TITLE_LOCK_TTL_S: Final = 120
PROVIDER: Final = "tvdb"

#: TheTVDB artwork types (v4 `artwork/types`): series poster, background, clear logo.
ARTWORK_POSTER: Final = 2
ARTWORK_BACKGROUND: Final = 3
ARTWORK_CLEARLOGO: Final = 23

#: TheTVDB genre names onto TMDB's TV genres (id, English name), so the categories map
#: (`metadata.genre_category_map`, by TMDB id) and the Arabic genre names apply as well.
GENRES_TO_TMDB: Final[Mapping[str, tuple[int, str]]] = {
    "action": (10759, "Action & Adventure"),
    "adventure": (10759, "Action & Adventure"),
    "martial arts": (10759, "Action & Adventure"),
    "animation": (16, "Animation"),
    "anime": (16, "Animation"),
    "children": (10762, "Kids"),
    "comedy": (35, "Comedy"),
    "crime": (80, "Crime"),
    "documentary": (99, "Documentary"),
    "drama": (18, "Drama"),
    "family": (10751, "Family"),
    "fantasy": (10765, "Sci-Fi & Fantasy"),
    "science fiction": (10765, "Sci-Fi & Fantasy"),
    "mystery": (9648, "Mystery"),
    "suspense": (9648, "Mystery"),
    "thriller": (9648, "Mystery"),
    "horror": (9648, "Mystery"),
    "news": (10763, "News"),
    "reality": (10764, "Reality"),
    "game show": (10764, "Reality"),
    "food": (10764, "Reality"),
    "home and garden": (10764, "Reality"),
    "travel": (10764, "Reality"),
    "soap": (10766, "Soap"),
    "talk show": (10767, "Talk"),
    "war": (10768, "War & Politics"),
    "western": (37, "Western"),
}

#: ISO 3166 alpha-3 (TheTVDB) to alpha-2 (our `countries`, the certification setting):
#: the countries this platform's audience and its usual catalogue come from.
_COUNTRIES: Final[Mapping[str, str]] = {
    "sau": "SA", "are": "AE", "kwt": "KW", "qat": "QA", "bhr": "BH", "omn": "OM",
    "egy": "EG", "jor": "JO", "lbn": "LB", "syr": "SY", "irq": "IQ", "mar": "MA",
    "dza": "DZ", "tun": "TN", "usa": "US", "gbr": "GB", "can": "CA", "aus": "AU",
    "fra": "FR", "deu": "DE", "esp": "ES", "ita": "IT", "tur": "TR", "ind": "IN",
    "pak": "PK", "kor": "KR", "jpn": "JP", "chn": "CN", "mex": "MX", "bra": "BR",
}  # fmt: skip
#: ISO 639-2 (TheTVDB) to ISO 639-1 (TMDB's `original_language`).
_LANGUAGES: Final[Mapping[str, str]] = {
    "ara": "ar", "eng": "en", "fra": "fr", "spa": "es", "deu": "de", "ita": "it",
    "tur": "tr", "hin": "hi", "urd": "ur", "kor": "ko", "jpn": "ja", "zho": "zh",
    "por": "pt", "rus": "ru", "fas": "fa",
}  # fmt: skip
_YOUTUBE: Final = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})")
_SEASON_TYPES: Final[Mapping[str, SeasonType]] = {
    EpisodeOrdering.TVDB_DVD: "dvd",
}


# --- The client -----------------------------------------------------------------------------------

# (configuration fingerprint, client or None) of the current TheTVDB settings in this process.
_client: tuple[str, TVDBClient | None] | None = None
_client_lock = threading.Lock()


def tvdb() -> TVDBClient | None:
    """The process's TheTVDB client, or None without a key (no fallback). It follows the
    admin's settings without a restart, like `services.tmdb()`."""
    global _client  # noqa: PLW0603 (process-local, replaced under the lock)
    config = tvdb_config()
    current = _client
    if current is not None and current[0] == config.fingerprint:
        return current[1]
    with _client_lock:
        if _client is None or _client[0] != config.fingerprint:
            _client = (config.fingerprint, client_for(config))
        return _client[1]


def require_client() -> TVDBClient:
    """The client for an admin's explicit TheTVDB action; a problem without a key."""
    client = tvdb()
    if client is None:
        raise ProblemError(
            ErrorCode.CONFLICT,
            "TheTVDB isn't set up: add its API key in Settings, Integrations.",
        )
    return client


def reset_caches() -> None:
    """Forget the client (tests)."""
    global _client  # noqa: PLW0603
    with _client_lock:
        _client = None


# --- Matching -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Fallback:
    """What the fallback did: placed the file (or opened its review) itself, or the reason
    and the candidates (both providers', best first) for the review."""

    handled: bool
    reason: str = "no_candidates"
    candidates: tuple[ScoredCandidate, ...] = ()


def tmdb_id_of(remote_ids: object) -> int | None:
    """The TMDB id among TheTVDB's remote ids (`sourceName` "TheMovieDB.com")."""
    for item in remote_ids if isinstance(remote_ids, list) else []:
        if not isinstance(item, dict):
            continue
        source = _str(item.get("sourceName")).lower()
        value = _str(str(item.get("id", "")))
        if "themoviedb" in source and value.isdigit():
            return int(value)
    return None


def decide_series(query: MatchQuery, parsed: ParseResult, client: TVDBClient) -> MatchDecision:
    """TheTVDB's candidates for the parsed series, scored like TMDB's."""
    threshold, margin = _thresholds()
    results: list[dict[str, Any]] = []
    for title in [t for t in dict.fromkeys((parsed.title, parsed.alternative_title)) if t]:
        results = client.search_series(title, year=parsed.year, limit=SEARCH_LIMIT)
        if not results and parsed.year:
            results = client.search_series(title, limit=SEARCH_LIMIT)
        if results:
            break
    candidates = [Candidate.from_tvdb(item) for item in results[:SEARCH_LIMIT]]
    decision = decide(
        query, [c for c in candidates if c.provider_id > 0], threshold=threshold, margin=margin
    )
    if decision.candidates:
        MATCH_CONFIDENCE.observe(decision.confidence)
    return decision


def fallback(
    file: MediaFile,
    parsed: ParseResult,
    query: MatchQuery,
    tmdb_decision: MatchDecision,
    tmdb_client: TMDBClient,
) -> Fallback:
    """Ask TheTVDB after TMDB found no confident series (see the module notes)."""
    keep = Fallback(False, tmdb_decision.reason, tmdb_decision.candidates)
    client = tvdb()
    if client is None:
        return keep
    try:
        decision = decide_series(query, parsed, client)
    except TVDBError as exc:
        logger.warning("tvdb.fallback_unavailable", extra={"error": type(exc).__name__})
        return keep
    accepted = decision.accepted
    if accepted is not None:
        try:
            accept_episode(
                file,
                accepted.candidate.provider_id,
                parsed,
                accepted.score,
                client,
                tmdb_client=tmdb_client,
            )
        except PlacementError as exc:
            services._open_review(
                file, ReviewKind.TV, exc.reason, decision.candidates, accepted.score
            )
        except TVDBError as exc:
            logger.warning("tvdb.fallback_unavailable", extra={"error": type(exc).__name__})
            return keep
        return Fallback(True)
    merged = sorted(
        (*tmdb_decision.candidates, *decision.candidates),
        key=lambda scored: (-scored.score, scored.candidate.provider != "tmdb"),
    )[:REVIEW_LIMIT]
    reason = decision.reason if not tmdb_decision.candidates else tmdb_decision.reason
    if decision.reason == "ambiguous":
        reason = "ambiguous"
    return Fallback(False, reason, tuple(merged))


def match_by_id(file: MediaFile, parsed: ParseResult, tmdb_client: TMDBClient) -> bool:
    """A `[tvdbid-N]` tag TMDB doesn't know: TheTVDB's series N at confidence 1.0.
    False when there is no TheTVDB key or TheTVDB doesn't know it either."""
    client = tvdb()
    tvdb_id = parsed.provider_ids.tvdb
    if client is None or not tvdb_id:
        return False
    try:
        accept_episode(file, tvdb_id, parsed, 1.0, client, tmdb_client=tmdb_client)
    except TVDBNotFoundError:
        return False
    except PlacementError as exc:
        services._open_review(file, ReviewKind.TV, exc.reason, ())
    except TVDBError as exc:
        logger.warning("tvdb.fallback_unavailable", extra={"error": type(exc).__name__})
        return False
    return True


def accept_episode(  # noqa: PLR0913 (the TMDB client and the actor are keyword-only)
    file: MediaFile,
    tvdb_id: int,
    parsed: ParseResult,
    confidence: float,
    client: TVDBClient,
    *,
    tmdb_client: TMDBClient | None = None,
    actor: User | None = None,
) -> Series:
    """Link an episode file to the TheTVDB series `tvdb_id`: on the series we hold for it,
    through TMDB when TheTVDB knows its TMDB id, else on a series built from TheTVDB."""
    if parsed.season is None or not parsed.episodes:
        raise PlacementError(
            "numbering",
            "The name has no season and episode numbers (absolute or dated numbering).",
        )
    held = Series.objects.filter(tvdb_id=tvdb_id).order_by("tmdb_id").first()
    tmdb_id = held.tmdb_id if held is not None else None
    if held is None:
        tmdb_id = tmdb_id_of(client.series_extended(tvdb_id).get("remoteIds"))
    if tmdb_id is not None:
        try:
            return services.accept_episode(
                file, tmdb_id, parsed, confidence, tmdb_client, actor=actor
            )
        except TMDBNotFoundError as exc:
            if isinstance(exc, TVDBNotFoundError) or held is not None:
                raise
            # TMDB doesn't know the id TheTVDB gave: build the series from TheTVDB.
    series = upsert_series(tvdb_id, client)
    season = ensure_season(series, parsed.season, client)
    services.link_episode_file(
        file,
        series,
        services.episodes_of(season, parsed),
        confidence,
        actor=actor,
        provider=PROVIDER,
        provider_id=tvdb_id,
    )
    return series


def search(kind: str, query: str, year: int | None = None) -> list[dict[str, Any]]:
    """TheTVDB search for the admin's manual matching (series only)."""
    if kind != ReviewKind.TV:
        return []
    client = require_client()
    try:
        results = client.search_series(query, year=year, limit=SEARCH_LIMIT * 2)
    except TVDBError as exc:
        raise services.provider_problem(exc, PROVIDER) from None
    found = []
    for item in results:
        candidate = Candidate.from_tvdb(item)
        if candidate.provider_id <= 0:
            continue
        found.append(
            {
                "provider": PROVIDER,
                "kind": candidate.kind,
                "id": candidate.provider_id,
                "title": candidate.title,
                "original_title": candidate.original_title,
                "year": candidate.year,
                "overview": candidate.overview,
                "popularity": candidate.popularity,
                "poster_path": candidate.poster_path,
            }
        )
    return found


# --- Enrichment -----------------------------------------------------------------------------------


def _lock(tvdb_id: int) -> Any:
    return state_redis().lock(
        f"lock:tvdb:tv:{tvdb_id}", timeout=TITLE_LOCK_TTL_S, blocking_timeout=TITLE_LOCK_TTL_S
    )


def _own_series(tvdb_id: int) -> Series | None:
    return Series.objects.filter(tvdb_id=tvdb_id, tmdb_id__isnull=True).first()


def upsert_series(tvdb_id: int, client: TVDBClient, *, refresh: bool = False) -> Series:
    """The TheTVDB-sourced series `tvdb_id`, built when new or when `refresh`."""
    series = _own_series(tvdb_id)
    if series is not None and series.metadata_refreshed_at is not None and not refresh:
        return series
    try:
        with _lock(tvdb_id):
            series = _own_series(tvdb_id)
            if series is not None and series.metadata_refreshed_at is not None and not refresh:
                return series
            details = client.series_extended(tvdb_id)
            with transaction.atomic():
                series = series or Series(
                    tvdb_id=tvdb_id, episode_ordering=EpisodeOrdering.TVDB_AIRED
                )
                apply_series(series, details)
                _save_title(series)
                _apply_relations(series, details)
                _apply_seasons(series, details)
                notify_catalog_changed("series", [series.pk])
    except LockError:
        msg = f"Timed out waiting for TheTVDB series {tvdb_id} to be built"
        raise TVDBError(msg) from None
    return series


def _translations(details: Mapping[str, Any], block: str, field: str) -> dict[str, str]:
    translations = details.get("translations")
    items = translations.get(block) if isinstance(translations, dict) else None
    found: dict[str, str] = {}
    for item in items or []:
        if isinstance(item, dict) and _str(item.get("language")) and _str(item.get(field)):
            found.setdefault(_str(item.get("language")), _str(item.get(field)))
    return found


def apply_series(series: Series, details: Mapping[str, Any]) -> None:
    """Copy TheTVDB's series record onto `series` (locked fields stay as the admin left
    them)."""
    names = _translations(details, "nameTranslations", "name")
    overviews = _translations(details, "overviewTranslations", "overview")
    own_name = _str(details.get("name"))
    title = names.get("eng") or own_name or "Untitled"
    first_air = _date(details.get("firstAired"))
    aliases = [
        _str(item.get("name")) for item in details.get("aliases") or [] if isinstance(item, dict)
    ]
    known = {title, own_name}
    alternatives = [
        name for name in dict.fromkeys((*aliases, *names.values())) if name and name not in known
    ]
    country = _COUNTRIES.get(_str(details.get("originalCountry")).lower(), "")
    values: dict[str, Any] = {
        "title": title,
        "original_title": own_name if own_name != title else "",
        "overview": overviews.get("eng") or _str(details.get("overview")),
        "first_air_date": first_air,
        "last_air_date": _date(details.get("lastAired")),
        "year": first_air.year if first_air else _year(details.get("year")),
        "episode_run_time": _positive(details.get("averageRuntime")),
        "imdb_id": _imdb_id(details.get("remoteIds"))[:16],
        "title_ar": _arabic(names.get("ara"), title),
        "overview_ar": _arabic(overviews.get("ara"), overviews.get("eng", "")),
        "alt_titles": [name[:255] for name in alternatives][:20],
        "countries": [country] if country else [],
        "certification": _certification(details.get("contentRatings")),
        "original_language": _LANGUAGES.get(_str(details.get("originalLanguage")), ""),
        "trailer_youtube_key": _trailer(details.get("trailers")),
    }
    _assign(series, values, set(series.metadata_locked_fields))
    series.tvdb_id = _positive(details.get("id")) or series.tvdb_id
    series.metadata_source = MetadataSource.TVDB


def _year(value: object) -> int | None:
    text = _str(str(value)) if value is not None else ""
    return int(text[:4]) if text[:4].isdigit() else None


def _imdb_id(remote_ids: object) -> str:
    for item in remote_ids if isinstance(remote_ids, list) else []:
        if isinstance(item, dict) and "imdb" in _str(item.get("sourceName")).lower():
            value = _str(str(item.get("id", "")))
            if value.startswith("tt"):
                return value
    return ""


def _certification(ratings: object) -> str:
    wanted = str(get_setting("metadata.certification_country"))
    by_country: dict[str, str] = {}
    for item in ratings if isinstance(ratings, list) else []:
        if isinstance(item, dict) and _str(item.get("name")):
            code = _COUNTRIES.get(_str(item.get("country")).lower(), "")
            by_country.setdefault(code, _str(item.get("name")))
    return (by_country.get(wanted) or by_country.get("US") or "")[:16]


def _trailer(trailers: object) -> str:
    for item in trailers if isinstance(trailers, list) else []:
        match = _YOUTUBE.search(_str(item.get("url"))) if isinstance(item, dict) else None
        if match:
            return match.group(1)
    return ""


def _apply_relations(series: Series, details: Mapping[str, Any]) -> None:
    tmdb_genres: dict[int, str] = {}
    for item in details.get("genres") or []:
        name = _str(item.get("name")).lower() if isinstance(item, dict) else ""
        if name in GENRES_TO_TMDB:
            genre_id, genre_name = GENRES_TO_TMDB[name]
            tmdb_genres.setdefault(genre_id, genre_name)
    genres = _genres(
        {"genres": [{"id": key, "name": value} for key, value in tmdb_genres.items()]}, "tv"
    )
    series.genres.set(genres)
    if "categories" not in series.metadata_locked_fields:
        series.categories.add(
            *categories_for_genres(
                "series", [(g.tmdb_id or 0, g.name_en, g.name_ar) for g in genres]
            )
        )
    _apply_credits(series, details.get("characters"))


def _apply_credits(series: Series, characters: object) -> None:
    rows: list[tuple[int, str, str, str]] = []  # (person id, name, role, character)
    cast = 0
    seen: set[tuple[int, str]] = set()
    items = [c for c in characters if isinstance(c, dict)] if isinstance(characters, list) else []
    items.sort(key=lambda c: _int(c.get("sort")) or 0)
    for item in items:
        person_id, name = _positive(item.get("peopleId")), _str(item.get("personName"))[:255]
        kind = _str(item.get("peopleType")).lower()
        role = {
            "actor": CreditRole.CAST,
            "director": CreditRole.DIRECTOR,
            "writer": CreditRole.WRITER,
        }.get(kind)
        if person_id is None or not name or role is None or (person_id, role) in seen:
            continue
        if role == CreditRole.CAST:
            if cast >= CAST_LIMIT:
                continue
            cast += 1
        seen.add((person_id, role))
        rows.append((person_id, name, role, _str(item.get("name"))[:255]))
    ids = sorted({row[0] for row in rows})
    Person.objects.bulk_create(
        [Person(tvdb_id=person_id, name=name) for person_id, name, _, _ in rows],
        ignore_conflicts=True,
    )
    people = {p.tvdb_id: p for p in Person.objects.filter(tvdb_id__in=ids)}
    Credit.objects.filter(series=series).delete()
    Credit.objects.bulk_create(
        [
            Credit(series=series, person=people[pid], role=role, character=character, order=order)
            for order, (pid, _, role, character) in enumerate(rows)
            if pid in people
        ]
    )


def season_type(series: Series) -> SeasonType:
    """The TheTVDB order a series' seasons follow (`episode_ordering`)."""
    return _SEASON_TYPES.get(series.episode_ordering, "official")


def _apply_seasons(series: Series, details: Mapping[str, Any]) -> None:
    wanted = season_type(series)
    for item in details.get("seasons") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type") if isinstance(item.get("type"), dict) else {}
        number = _int(item.get("number"))
        if number is None or _str((kind or {}).get("type")) != wanted:
            continue
        season, _ = Season.objects.get_or_create(series=series, number=number)
        season.tvdb_id = _positive(item.get("id"))
        season.name = season.name or (f"Season {number}" if number else "Specials")
        season.save()


def ensure_season(
    series: Series, number: int, client: TVDBClient, *, refresh: bool = False
) -> Season:
    """The season with its episodes in the series' TheTVDB order, from TheTVDB."""
    season = Season.objects.filter(series=series, number=number).first()
    if season is not None and season.episodes.exists() and not refresh:
        return season
    if series.tvdb_id is None:
        raise PlacementError("episode_not_found", f"{series.title} has no TheTVDB id.")
    order = season_type(series)
    episodes = client.series_episodes(series.tvdb_id, season_type=order, season=number)
    episodes = [e for e in episodes if _int(e.get("seasonNumber")) == number]
    if not episodes:
        raise PlacementError(
            "episode_not_found", f"TheTVDB has no season {number} of {series.title}."
        )
    try:
        arabic = {
            _int(e.get("number")): e
            for e in client.series_episodes(
                series.tvdb_id, season_type=order, language="ara", season=number
            )
        }
    except TVDBError:
        arabic = {}
    with transaction.atomic():
        season, _ = Season.objects.get_or_create(series=series, number=number)
        if not season.name:
            season.name = f"Season {number}" if number else "Specials"
        season.episode_count = max(season.episode_count, len(episodes))
        aired = [d for d in (_date(e.get("aired")) for e in episodes) if d is not None]
        season.air_date = season.air_date or (min(aired) if aired else None)
        season.save()
        existing = {episode.number: episode for episode in season.episodes.all()}
        for item in episodes:
            position = _int(item.get("number"))
            if position is None:
                continue
            episode = existing.get(position) or Episode(season=season, number=position)
            episode.tvdb_id = _positive(item.get("id"))
            episode.absolute_number = _positive(item.get("absoluteNumber"))
            episode.title = _str(item.get("name"))[:255]
            episode.overview = _str(item.get("overview"))
            episode.air_date = _date(item.get("aired"))
            episode.runtime_min = _positive(item.get("runtime"))
            episode.still_path = _str(item.get("image"))[:255]
            ar_item = arabic.get(position) or {}
            episode.title_ar = _arabic(ar_item.get("name"), episode.title)[:255]
            episode.overview_ar = _arabic(ar_item.get("overview"), episode.overview)
            episode.save()
    return season


def refresh_series(series: Series, client: TVDBClient | None = None) -> Series:
    """Fetch a TheTVDB-sourced series and its seasons with episodes again."""
    client = client or require_client()
    if series.tvdb_id is None:
        raise ProblemError(ErrorCode.CONFLICT, "This series has no TheTVDB id to refresh from.")
    series = upsert_series(series.tvdb_id, client, refresh=True)
    for season in Season.objects.filter(series=series, episodes__isnull=False).distinct():
        try:
            ensure_season(series, season.number, client, refresh=True)
        except PlacementError:
            logger.warning("season vanished from the provider", extra={"season": season.number})
    transaction.on_commit(lambda: services._queue_images("series", series.pk))
    return series


# --- Artwork --------------------------------------------------------------------------------------


def _best(artworks: Iterable[dict[str, Any]], kind: int, languages: Sequence[str | None]) -> str:
    candidates = [a for a in artworks if _int(a.get("type")) == kind and _str(a.get("image"))]
    for language in languages:
        chosen = [a for a in candidates if (a.get("language") or None) == language]
        if chosen:
            chosen.sort(key=lambda a: -(_int(a.get("score")) or 0))
            return _str(chosen[0].get("image"))
    return ""


def image_picks(details: Mapping[str, Any]) -> list[ImagePick]:
    """A TheTVDB-sourced series' artwork: its poster (and an Arabic one), the best
    background and a clear logo."""
    artworks = [a for a in details.get("artworks") or [] if isinstance(a, dict)]
    picks: list[ImagePick] = []
    poster = _str(details.get("image")) or _best(artworks, ARTWORK_POSTER, ("eng", None))
    if poster:
        picks.append(ImagePick(ImageKind.POSTER, poster, "", True))
    arabic = _best(artworks, ARTWORK_POSTER, ("ara",))
    if arabic and arabic != poster:
        picks.append(ImagePick(ImageKind.POSTER, arabic, "ar", not poster))
    backdrop = _best(artworks, ARTWORK_BACKGROUND, (None, "eng"))
    if backdrop:
        picks.append(ImagePick(ImageKind.BACKDROP, backdrop, "", True))
    logo = _best(artworks, ARTWORK_CLEARLOGO, ("eng", "ara", None))
    if logo:
        picks.append(ImagePick(ImageKind.LOGO, logo, "", True))
    return [pick for pick in picks if len(pick.path) <= 255]


def _store(
    owner: Mapping[str, Any], key: str, picks: Sequence[ImagePick], client: TVDBClient
) -> int:
    return services.store_picks(
        owner,
        key,
        picks,
        open_http=client.open_artwork_client,
        url_for=lambda path: path,
        source=ImageSource.TVDB,
    )


def fetch_series_images(series: Series, client: TVDBClient | None = None) -> int:
    """Series artwork from TheTVDB, plus season posters and episode stills with files.
    Nothing without a key (the series keeps what it has)."""
    client = client or tvdb()
    if client is None or series.tvdb_id is None or series.metadata_source != MetadataSource.TVDB:
        return 0
    details = client.series_extended(series.tvdb_id)
    added = _store({"series": series}, f"series/{series.pk}", image_picks(details), client)
    wanted = season_type(series)
    season_posters: dict[int | None, str] = {}
    for item in details.get("seasons") or []:
        kind = item.get("type") if isinstance(item, dict) else None
        if isinstance(kind, dict) and _str(kind.get("type")) == wanted and _str(item.get("image")):
            season_posters[_int(item.get("number"))] = _str(item.get("image"))
    with_files = MediaFile.objects.filter(removed_at__isnull=True)
    seasons = Season.objects.filter(series=series, episodes__files__in=with_files).distinct()
    for season in seasons:
        path = season_posters.get(season.number)
        if path:
            added += _store(
                {"season": season},
                f"season/{season.pk}",
                [ImagePick(ImageKind.POSTER, path, "", True)],
                client,
            )
    episodes = (
        Episode.objects.filter(season__series=series, files__in=with_files)
        .exclude(still_path="")
        .distinct()
    )
    for episode in episodes:
        added += _store(
            {"episode": episode},
            f"episode/{episode.pk}",
            [ImagePick(ImageKind.STILL, episode.still_path, "", True)],
            client,
        )
    if added:
        notify_catalog_changed("series", [series.pk])
    return added
