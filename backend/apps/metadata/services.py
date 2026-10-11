"""Metadata: match files to TMDB, enrich titles, store artwork, run the review queue
(SPEC §7.2 steps 4-9).

- Matching: a provider id in the path gives confidence 1.0; otherwise TMDB search
  results are scored by P2 `scoring.decide` with the thresholds from the settings
  `metadata.match_auto_accept` and `metadata.match_margin`. A shortlist that is not
  accepted is scored again from each candidate's details (runtime, alternative and
  translated titles). Anything still undecided becomes a `MatchReview`
  with the top five candidates and their score breakdowns.
- Enrichment: details with credits, videos, images, release dates (or content ratings),
  alternative titles and translations in one request; the Arabic title, overview and
  tagline come from the `ar` translation (left empty when there is none). Genres map
  to categories (`metadata.genre_category_map`); the certification comes from
  `metadata.certification_country`, else the US. Fields an admin locked are kept.
- Fixture mode (no TMDB credential): the same code path over P2's synthetic fixtures;
  titles are marked `metadata_source=synthetic`.
- Artwork: primary poster (plus an Arabic poster when TMDB has one), backdrop and logo
  per title, season posters and episode stills for what has files, converted by P2
  `images` into `DATA_ROOT/images` and recorded as `MediaImage` rows.
"""

import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any, Final, cast

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from redis.exceptions import LockError

from apps.accounts.models import User
from apps.audit import services as audit
from apps.catalog.models import (
    Credit,
    CreditRole,
    Episode,
    FileState,
    Genre,
    ImageKind,
    MatchReview,
    MediaFile,
    MediaImage,
    MetadataSource,
    Movie,
    Person,
    ReviewKind,
    ReviewStatus,
    Season,
    Series,
    Title,
)
from apps.catalog.services import (
    categories_for_genres,
    refresh_movie_status,
    refresh_series_status,
    refresh_status_of_files,
)
from apps.catalog.signals import notify_catalog_changed
from apps.core.errors import ErrorCode, ProblemError
from apps.core.metrics import MATCH_CONFIDENCE
from apps.core.services import get_setting
from apps.core.stores import state_redis
from apps.library.parsing import ParseResult
from apps.metadata.images import ImageError, LocalImageStore, fetch_and_store, tmdb_source_size
from apps.metadata.images import ImageKind as ImageKindName
from apps.metadata.scoring import (
    Candidate,
    CandidateKind,
    MatchDecision,
    MatchQuery,
    ScoredCandidate,
    decide,
)
from apps.metadata.tmdb import JSONObject, TMDBClient, TMDBError, TMDBNotFoundError, image_url
from apps.metadata.tmdb.factory import client_from_settings

logger = logging.getLogger(__name__)

#: Search results considered per query (TMDB pages hold 20).
SEARCH_LIMIT: Final = 10
CAST_LIMIT: Final = 15
ALT_TITLE_LIMIT: Final = 20
ARABIC_LANGUAGE: Final = "ar-SA"
TITLE_LOCK_TTL_S: Final = 120
_ARABIC = re.compile(r"[؀-ۿ]")


class PlacementError(Exception):
    """An episode file cannot be placed in its series (`reason` is a MatchReview reason)."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


# --- The client -----------------------------------------------------------------------------------


@cache
def tmdb() -> TMDBClient:
    """The process's TMDB client: live with a credential, fixture mode without."""
    return client_from_settings()


def source_of(client: TMDBClient) -> str:
    return MetadataSource.SYNTHETIC if client.fixture_mode else MetadataSource.TMDB


@cache
def _genre_names_ar(kind: str) -> Mapping[int, str]:
    client = tmdb()
    try:
        data = (
            client.movie_genres(language=ARABIC_LANGUAGE)
            if kind == "movie"
            else client.tv_genres(language=ARABIC_LANGUAGE)
        )
    except TMDBError:
        logger.warning("Arabic genre names unavailable", extra={"kind": kind})
        return {}
    names = {}
    for genre in data.get("genres", []):
        name = str(genre.get("name", ""))
        if isinstance(genre.get("id"), int) and _ARABIC.search(name):
            names[genre["id"]] = name
    return names


def reset_caches() -> None:
    """Forget the client and the genre names (tests, or after a credential change)."""
    tmdb.cache_clear()
    _genre_names_ar.cache_clear()


# --- Matching -------------------------------------------------------------------------------------


def _thresholds() -> tuple[float, float]:
    return (
        float(get_setting("metadata.match_auto_accept")),
        float(get_setting("metadata.match_margin")),
    )


def _runtime_min(file: MediaFile) -> float | None:
    return file.duration_ms / 60000 if file.duration_ms else None


def match_file(file: MediaFile, client: TMDBClient | None = None) -> MediaFile:
    """Match a probed file to a movie or a series episode, or open a review."""
    client = client or tmdb()
    parsed = ParseResult.from_json(file.parse_result)
    if parsed.kind == "movie":
        _match_movie(file, parsed, client)
    elif parsed.kind == "episode":
        _match_episode(file, parsed, client)
    else:
        _open_review(file, ReviewKind.MOVIE, "classification", ())
    return file


def _provider_movie_id(parsed: ParseResult, client: TMDBClient) -> int | None:
    ids = parsed.provider_ids
    if ids.tmdb:
        return ids.tmdb
    if ids.imdb:
        found = client.find(ids.imdb, "imdb_id").get("movie_results") or []
        return int(found[0]["id"]) if found else None
    return None


def _provider_series_id(parsed: ParseResult, client: TMDBClient) -> int | None:
    ids = parsed.provider_ids
    if ids.tmdb:
        return ids.tmdb
    for external, source in ((ids.tvdb, "tvdb_id"), (ids.imdb, "imdb_id")):
        if external:
            found = client.find(str(external), source).get("tv_results") or []  # type: ignore[arg-type]
            if found:
                return int(found[0]["id"])
    return None


def _search(client: TMDBClient, kind: str, title: str, year: int | None) -> list[JSONObject]:
    if kind == "movie":
        results = client.search_movie(title, year=year).get("results") or []
        if not results and year:
            results = client.search_movie(title).get("results") or []
    else:
        results = client.search_tv(title, first_air_date_year=year).get("results") or []
        if not results and year:
            results = client.search_tv(title).get("results") or []
    return [item for item in results if isinstance(item, dict)][:SEARCH_LIMIT]


def _decide(
    query: MatchQuery,
    parsed: ParseResult,
    kind: str,
    client: TMDBClient,
) -> MatchDecision:
    threshold, margin = _thresholds()
    titles = [t for t in dict.fromkeys((parsed.title, parsed.alternative_title)) if t]
    results: list[JSONObject] = []
    for title in titles:
        results = _search(client, kind, title, parsed.year)
        if results:
            break
    candidate_kind: CandidateKind = "movie" if kind == "movie" else "tv"
    candidates = [Candidate.from_tmdb(item, candidate_kind) for item in results]
    decision = decide(query, candidates, threshold=threshold, margin=margin)
    if decision.needs_review and decision.candidates:
        # The shortlist (top five) again, from details: runtimes, alternative and
        # translated titles. A runtime counts only when every candidate has one;
        # otherwise the ones with details would be judged on a signal the others are spared.
        refined = [_details_candidate(scored, client) for scored in decision.candidates]
        if any(candidate.runtime_min is None for candidate in refined):
            refined = [replace(candidate, runtime_min=None) for candidate in refined]
        decision = decide(query, refined, threshold=threshold, margin=margin)
    if decision.candidates:
        MATCH_CONFIDENCE.observe(decision.confidence)
    return decision


def _details_candidate(scored: ScoredCandidate, client: TMDBClient) -> Candidate:
    candidate = scored.candidate
    try:
        if candidate.kind == "movie":
            details = client.movie_details(candidate.provider_id)
        else:
            details = client.tv_details(candidate.provider_id)
    except TMDBNotFoundError:
        return candidate
    return Candidate.from_tmdb(details, candidate.kind)


def _match_movie(file: MediaFile, parsed: ParseResult, client: TMDBClient) -> None:
    provider_id = _provider_movie_id(parsed, client)
    if provider_id is not None:
        try:
            accept_movie(file, provider_id, 1.0, client)
        except TMDBNotFoundError:
            _open_review(file, ReviewKind.MOVIE, "no_candidates", ())
        return
    if not parsed.title:
        _open_review(file, ReviewKind.MOVIE, "no_candidates", ())
        return
    query = MatchQuery(title=parsed.title, year=parsed.year, runtime_min=_runtime_min(file))
    decision = _decide(query, parsed, "movie", client)
    accepted = decision.accepted
    if accepted is not None and not parsed.ambiguous:
        accept_movie(file, accepted.candidate.provider_id, accepted.score, client)
        return
    reason = "classification" if parsed.ambiguous else decision.reason
    _open_review(file, ReviewKind.MOVIE, reason, decision.candidates, decision.confidence)


def _match_episode(file: MediaFile, parsed: ParseResult, client: TMDBClient) -> None:
    provider_id = _provider_series_id(parsed, client)
    candidates: Sequence[ScoredCandidate] = ()
    confidence = 1.0
    if provider_id is None:
        if not parsed.title:
            _open_review(file, ReviewKind.TV, "no_candidates", ())
            return
        query = MatchQuery(title=parsed.title, year=parsed.year)
        decision = _decide(query, parsed, "tv", client)
        candidates, confidence = decision.candidates, decision.confidence
        accepted = decision.accepted
        if accepted is None or parsed.ambiguous:
            reason = "classification" if parsed.ambiguous else decision.reason
            _open_review(file, ReviewKind.TV, reason, candidates, confidence)
            return
        provider_id = accepted.candidate.provider_id
    try:
        accept_episode(file, provider_id, parsed, confidence, client)
    except TMDBNotFoundError:
        _open_review(file, ReviewKind.TV, "no_candidates", candidates)
    except PlacementError as exc:
        _open_review(file, ReviewKind.TV, exc.reason, candidates, confidence)


def _open_review(
    file: MediaFile,
    kind: str,
    reason: str,
    candidates: Sequence[ScoredCandidate],
    confidence: float | None = None,
) -> MatchReview:
    with transaction.atomic():
        review = (
            MatchReview.objects.select_for_update()
            .filter(media_file=file, status=ReviewStatus.OPEN)
            .first()
        )
        payload = [scored.to_json() for scored in candidates]
        if review is None:
            review = MatchReview.objects.create(
                media_file=file,
                kind=kind,
                reason=reason,
                parse_result=file.parse_result,
                candidates=payload,
            )
        else:
            review.kind, review.reason = kind, reason
            review.parse_result, review.candidates = file.parse_result, payload
            review.save()
        file.state = FileState.REVIEW
        file.match_confidence = confidence if candidates else None
        file.save(update_fields=["state", "match_confidence", "updated_at"])
    return review


def _close_reviews(file: MediaFile, provider_id: int, actor: User | None) -> None:
    MatchReview.objects.filter(media_file=file, status=ReviewStatus.OPEN).update(
        status=ReviewStatus.RESOLVED,
        chosen_provider_id=provider_id,
        decided_by=actor,
        decided_at=timezone.now(),
        updated_at=timezone.now(),
    )


def accept_movie(
    file: MediaFile,
    tmdb_id: int,
    confidence: float,
    client: TMDBClient | None = None,
    *,
    actor: User | None = None,
) -> Movie:
    """Link a file to the TMDB movie `tmdb_id`, creating and enriching it if new."""
    client = client or tmdb()
    movie = upsert_movie(tmdb_id, client)
    previous = _owners(file)
    with transaction.atomic():
        file.movie = movie
        file.state = FileState.MATCHED
        file.match_confidence = confidence
        file.save(update_fields=["movie", "state", "match_confidence", "updated_at"])
        file.episodes.clear()
        if "categories" not in movie.metadata_locked_fields:
            movie.categories.add(*file.library.default_categories.all())
        _close_reviews(file, tmdb_id, actor)
        refresh_movie_status(movie)
        _refresh_previous(previous, keep=movie)
        notify_catalog_changed("movie", [movie.pk])
    return movie


def accept_episode(  # noqa: PLR0913 (the actor is keyword-only)
    file: MediaFile,
    tmdb_id: int,
    parsed: ParseResult,
    confidence: float,
    client: TMDBClient | None = None,
    *,
    actor: User | None = None,
) -> Series:
    """Link an episode file to its episodes of the TMDB series `tmdb_id` (several for a
    multi-episode file), creating the series, season and episodes if needed."""
    client = client or tmdb()
    if parsed.season is None or not parsed.episodes:
        raise PlacementError(
            "numbering",
            "The name has no season and episode numbers (absolute or dated numbering).",
        )
    series = upsert_series(tmdb_id, client)
    season = ensure_season(series, parsed.season, client)
    episodes = list(season.episodes.filter(number__in=parsed.episodes))
    missing = sorted(set(parsed.episodes) - {episode.number for episode in episodes})
    if missing:
        raise PlacementError(
            "episode_not_found",
            f"Season {parsed.season} of {series.title} has no episode "
            + ", ".join(str(n) for n in missing)
            + ".",
        )
    previous = _owners(file)
    with transaction.atomic():
        file.movie = None
        file.state = FileState.MATCHED
        file.match_confidence = confidence
        file.save(update_fields=["movie", "state", "match_confidence", "updated_at"])
        file.episodes.set(episodes)
        if "categories" not in series.metadata_locked_fields:
            series.categories.add(*file.library.default_categories.all())
        _close_reviews(file, tmdb_id, actor)
        refresh_series_status(series)
        _refresh_previous(previous, keep=series)
        notify_catalog_changed("series", [series.pk])
    transaction.on_commit(lambda: _queue_images("series", series.pk))
    return series


def _owners(file: MediaFile) -> list[Title]:
    owners: list[Title] = []
    if file.movie_id is not None:
        owners.extend(Movie.objects.filter(pk=file.movie_id))
    owners.extend(Series.objects.filter(seasons__episodes__files=file).distinct())
    return owners


def _refresh_previous(previous: Iterable[Title], *, keep: Title) -> None:
    for title in previous:
        if title.pk == keep.pk and type(title) is type(keep):
            continue
        if isinstance(title, Movie):
            refresh_movie_status(title)
        elif isinstance(title, Series):
            refresh_series_status(title)


# --- Enrichment -----------------------------------------------------------------------------------


def _title_lock(kind: str, tmdb_id: int) -> Any:
    return state_redis().lock(
        f"lock:tmdb:{kind}:{tmdb_id}", timeout=TITLE_LOCK_TTL_S, blocking_timeout=TITLE_LOCK_TTL_S
    )


def upsert_movie(tmdb_id: int, client: TMDBClient, *, refresh: bool = False) -> Movie:
    """The movie with this TMDB id, fetched and enriched when new or when `refresh`."""
    movie = Movie.objects.filter(tmdb_id=tmdb_id).first()
    if movie is not None and movie.metadata_refreshed_at is not None and not refresh:
        return movie
    try:
        with _title_lock("movie", tmdb_id):
            movie = Movie.objects.filter(tmdb_id=tmdb_id).first()
            if movie is not None and movie.metadata_refreshed_at is not None and not refresh:
                return movie
            details = client.movie_details(tmdb_id)
            with transaction.atomic():
                movie = movie or Movie(tmdb_id=tmdb_id)
                _apply_movie(movie, details, client)
                _save_title(movie)
                _apply_relations(movie, details, "movie")
                notify_catalog_changed("movie", [movie.pk])
    except LockError:
        raise TMDBError(f"Timed out waiting for movie {tmdb_id} to be enriched") from None
    saved = movie
    transaction.on_commit(lambda: _queue_images("movie", saved.pk))
    return saved


def upsert_series(tmdb_id: int, client: TMDBClient, *, refresh: bool = False) -> Series:
    """The series with this TMDB id (and its season list), fetched when new or `refresh`."""
    series = Series.objects.filter(tmdb_id=tmdb_id).first()
    if series is not None and series.metadata_refreshed_at is not None and not refresh:
        return series
    try:
        with _title_lock("tv", tmdb_id):
            series = Series.objects.filter(tmdb_id=tmdb_id).first()
            if series is not None and series.metadata_refreshed_at is not None and not refresh:
                return series
            details = client.tv_details(tmdb_id)
            with transaction.atomic():
                series = series or Series(tmdb_id=tmdb_id)
                _apply_series(series, details, client)
                _save_title(series)
                _apply_relations(series, details, "tv")
                _apply_seasons(series, details)
                notify_catalog_changed("series", [series.pk])
    except LockError:
        raise TMDBError(f"Timed out waiting for series {tmdb_id} to be enriched") from None
    return series


def _save_title(title: Movie | Series) -> None:
    title.metadata_refreshed_at = timezone.now()
    try:
        with transaction.atomic():
            title.save()
    except IntegrityError:
        # Another worker created it between our check and save (the lock timed out).
        raise TMDBError(f"Title with TMDB id {title.tmdb_id} was created concurrently") from None


def ensure_season(
    series: Series, number: int, client: TMDBClient, *, refresh: bool = False
) -> Season:
    """The season with its episodes, created from the provider when missing (SPEC §7.2
    step 6: a file may arrive before its season is in the catalogue)."""
    season = Season.objects.filter(series=series, number=number).first()
    if season is not None and season.episodes.exists() and not refresh:
        return season
    if series.tmdb_id is None:
        raise PlacementError("episode_not_found", f"{series.title} has no TMDB id.")
    try:
        data = client.tv_season(series.tmdb_id, number)
    except TMDBNotFoundError:
        raise PlacementError(
            "episode_not_found", f"TMDB has no season {number} of {series.title}."
        ) from None
    try:
        arabic = client.tv_season(series.tmdb_id, number, language=ARABIC_LANGUAGE)
    except TMDBError:
        arabic = {}
    with transaction.atomic():
        season, _ = Season.objects.get_or_create(series=series, number=number)
        season.tmdb_id = _int(data.get("id"))
        season.name = _str(data.get("name"))[:255]
        season.overview = _str(data.get("overview"))
        season.air_date = _date(data.get("air_date"))
        season.name_ar = _arabic(arabic.get("name"), season.name)[:255]
        season.overview_ar = _arabic(arabic.get("overview"), season.overview)
        episodes_data = [e for e in data.get("episodes") or [] if isinstance(e, dict)]
        season.episode_count = max(season.episode_count, len(episodes_data))
        season.save()
        arabic_episodes = {
            _int(e.get("episode_number")): e
            for e in arabic.get("episodes") or []
            if isinstance(e, dict)
        }
        existing = {episode.number: episode for episode in season.episodes.all()}
        for item in episodes_data:
            number_ = _int(item.get("episode_number"))
            if number_ is None:
                continue
            episode = existing.get(number_) or Episode(season=season, number=number_)
            episode.tmdb_id = _int(item.get("id"))
            episode.title = _str(item.get("name"))[:255]
            episode.overview = _str(item.get("overview"))
            episode.air_date = _date(item.get("air_date"))
            episode.runtime_min = _positive(item.get("runtime"))
            episode.rating = _float(item.get("vote_average"))
            episode.still_path = _str(item.get("still_path"))[:255]
            ar_item = arabic_episodes.get(number_, {})
            episode.title_ar = _arabic(ar_item.get("name"), episode.title)[:255]
            episode.overview_ar = _arabic(ar_item.get("overview"), episode.overview)
            episode.save()
    return season


def _apply_movie(movie: Movie, details: JSONObject, client: TMDBClient) -> None:
    locked = set(movie.metadata_locked_fields)
    release = _date(details.get("release_date"))
    arabic = _translation(details, "title")
    values: dict[str, Any] = {
        "title": _str(details.get("title")) or _str(details.get("original_title")) or "Untitled",
        "original_title": _str(details.get("original_title")),
        "overview": _str(details.get("overview")),
        "tagline": _str(details.get("tagline")),
        "release_date": release,
        "year": release.year if release else None,
        "runtime_min": _positive(details.get("runtime")),
        "imdb_id": _str(details.get("imdb_id"))[:16],
        "title_ar": arabic.get("title", ""),
        "overview_ar": arabic.get("overview", ""),
        "tagline_ar": arabic.get("tagline", ""),
        "alt_titles": _alt_titles(details.get("alternative_titles"), "titles", details),
        "countries": _countries(details),
        "certification": _movie_certification(details),
        **_common(details),
    }
    _assign(movie, values, locked)
    movie.metadata_source = source_of(client)


def _apply_series(series: Series, details: JSONObject, client: TMDBClient) -> None:
    locked = set(series.metadata_locked_fields)
    first_air = _date(details.get("first_air_date"))
    arabic = _translation(details, "name")
    run_times = details.get("episode_run_time")
    external = details.get("external_ids") if isinstance(details.get("external_ids"), dict) else {}
    values: dict[str, Any] = {
        "title": _str(details.get("name")) or _str(details.get("original_name")) or "Untitled",
        "original_title": _str(details.get("original_name")),
        "overview": _str(details.get("overview")),
        "tagline": _str(details.get("tagline")),
        "first_air_date": first_air,
        "last_air_date": _date(details.get("last_air_date")),
        "year": first_air.year if first_air else None,
        "episode_run_time": _positive(run_times[0])
        if isinstance(run_times, list) and run_times
        else None,
        "imdb_id": _str((external or {}).get("imdb_id"))[:16],
        "tvdb_id": _positive((external or {}).get("tvdb_id")),
        "title_ar": arabic.get("title", ""),
        "overview_ar": arabic.get("overview", ""),
        "tagline_ar": arabic.get("tagline", ""),
        "alt_titles": _alt_titles(details.get("alternative_titles"), "results", details),
        "countries": [str(c)[:2] for c in details.get("origin_country") or []],
        "certification": _tv_certification(details),
        **_common(details),
    }
    _assign(series, values, locked)
    series.metadata_source = source_of(client)


def _common(details: JSONObject) -> dict[str, Any]:
    return {
        "rating": _float(details.get("vote_average")),
        "vote_count": _positive(details.get("vote_count")) or 0,
        "popularity": _float(details.get("popularity")) or 0.0,
        "original_language": _str(details.get("original_language"))[:8],
        "trailer_youtube_key": _trailer(details),
    }


def _assign(title: Title, values: Mapping[str, Any], locked: set[str]) -> None:
    for name, value in values.items():
        if name in locked:
            continue
        cleaned = value
        if isinstance(value, str):
            max_length = getattr(title._meta.get_field(name), "max_length", None)
            cleaned = value[:max_length] if max_length else value
        setattr(title, name, cleaned)


def _apply_relations(title: Movie | Series, details: JSONObject, kind: str) -> None:
    genres = _genres(details, kind)
    title.genres.set(genres)
    if "categories" not in title.metadata_locked_fields:
        category_kind = "vod" if kind == "movie" else "series"
        title.categories.add(
            *categories_for_genres(
                category_kind, [(g.tmdb_id or 0, g.name_en, g.name_ar) for g in genres]
            )
        )
    _apply_credits(title, details)


def _genres(details: JSONObject, kind: str) -> list[Genre]:
    names_ar = _genre_names_ar(kind)
    genres = []
    for item in details.get("genres") or []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            continue
        tmdb_id, name = item["id"], _str(item.get("name"))[:100]
        genre, created = Genre.objects.get_or_create(
            tmdb_id=tmdb_id, defaults={"name_en": name, "name_ar": names_ar.get(tmdb_id, "")}
        )
        if not created and not genre.name_ar and names_ar.get(tmdb_id):
            genre.name_ar = names_ar[tmdb_id]
            genre.save(update_fields=["name_ar", "updated_at"])
        genres.append(genre)
    return genres


@dataclass(frozen=True, slots=True)
class _CreditRow:
    tmdb_id: int
    name: str
    profile_path: str
    role: str
    character: str
    order: int


def _credit_rows(details: JSONObject) -> list[_CreditRow]:
    credits = details.get("credits") if isinstance(details.get("credits"), dict) else {}
    rows: list[_CreditRow] = []
    cast = [c for c in (credits or {}).get("cast") or [] if isinstance(c, dict)]
    cast.sort(key=lambda c: c.get("order", 0) if isinstance(c.get("order"), int) else 0)
    for index, item in enumerate(cast[:CAST_LIMIT]):
        rows.append(_credit(item, CreditRole.CAST, _str(item.get("character")), index))
    seen: set[tuple[int, str]] = set()
    for item in (credits or {}).get("crew") or []:
        if not isinstance(item, dict):
            continue
        if item.get("job") == "Director":
            role = CreditRole.DIRECTOR
        elif item.get("department") == "Writing":
            role = CreditRole.WRITER
        else:
            continue
        row = _credit(item, role, _str(item.get("job")), len(rows))
        if (row.tmdb_id, role) not in seen:
            seen.add((row.tmdb_id, role))
            rows.append(row)
    for item in details.get("created_by") or []:
        if isinstance(item, dict) and (_int(item.get("id")), CreditRole.WRITER) not in seen:
            rows.append(_credit(item, CreditRole.WRITER, "Creator", len(rows)))
    return [row for row in rows if row.tmdb_id > 0 and row.name]


def _credit(item: Mapping[str, Any], role: str, character: str, order: int) -> _CreditRow:
    return _CreditRow(
        tmdb_id=_int(item.get("id")) or 0,
        name=_str(item.get("name"))[:255],
        profile_path=_str(item.get("profile_path"))[:255],
        role=role,
        character=character[:255],
        order=order,
    )


def _apply_credits(title: Movie | Series, details: JSONObject) -> None:
    rows = _credit_rows(details)
    ids = sorted({row.tmdb_id for row in rows})
    people = {p.tmdb_id: p for p in Person.objects.filter(tmdb_id__in=ids)}
    missing = [row for row in rows if row.tmdb_id not in people]
    Person.objects.bulk_create(
        [
            Person(tmdb_id=row.tmdb_id, name=row.name, profile_path=row.profile_path)
            for row in {row.tmdb_id: row for row in missing}.values()
        ],
        ignore_conflicts=True,
    )
    if missing:
        people = {p.tmdb_id: p for p in Person.objects.filter(tmdb_id__in=ids)}
    owner: dict[str, Movie | Series] = (
        {"movie": title} if isinstance(title, Movie) else {"series": title}
    )
    Credit.objects.filter(**owner).delete()
    Credit.objects.bulk_create(
        [
            Credit(
                **owner,
                person=people[row.tmdb_id],
                role=row.role,
                character=row.character,
                order=row.order,
            )
            for row in rows
            if row.tmdb_id in people
        ]
    )


def _apply_seasons(series: Series, details: JSONObject) -> None:
    for item in details.get("seasons") or []:
        number = _int(item.get("season_number")) if isinstance(item, dict) else None
        if number is None:
            continue
        season, _ = Season.objects.get_or_create(series=series, number=number)
        season.tmdb_id = _int(item.get("id"))
        season.name = _str(item.get("name"))[:255]
        season.overview = _str(item.get("overview"))
        season.air_date = _date(item.get("air_date"))
        season.episode_count = _positive(item.get("episode_count")) or 0
        season.save()


def _translation(details: JSONObject, title_key: str) -> dict[str, str]:
    """Arabic title, overview and tagline from appended `translations` (SA first)."""
    block = details.get("translations")
    entries = block.get("translations") if isinstance(block, dict) else None
    best: dict[str, Any] | None = None
    for entry in entries or []:
        if not isinstance(entry, dict) or entry.get("iso_639_1") != "ar":
            continue
        if best is None or entry.get("iso_3166_1") == "SA":
            best = entry
    data = best.get("data") if best else None
    if not isinstance(data, dict):
        return {}
    return {
        "title": _str(data.get(title_key)),
        "overview": _str(data.get("overview")),
        "tagline": _str(data.get("tagline")),
    }


def _alt_titles(block: object, list_key: str, details: JSONObject) -> list[str]:
    known = {_str(details.get("title")), _str(details.get("name"))}
    names = []
    items = block.get(list_key) if isinstance(block, dict) else None
    for item in items or []:
        name = _str(item.get("title")) if isinstance(item, dict) else ""
        if name and name not in known and name not in names:
            names.append(name[:255])
    return names[:ALT_TITLE_LIMIT]


def _countries(details: JSONObject) -> list[str]:
    codes = [str(c) for c in details.get("origin_country") or [] if isinstance(c, str)]
    if not codes:
        codes = [
            str(c.get("iso_3166_1"))
            for c in details.get("production_countries") or []
            if isinstance(c, dict) and c.get("iso_3166_1")
        ]
    return [code[:2] for code in dict.fromkeys(codes)]


def _movie_certification(details: JSONObject) -> str:
    block = details.get("release_dates")
    results = block.get("results") if isinstance(block, dict) else None
    by_country: dict[str, str] = {}
    for entry in results or []:
        if not isinstance(entry, dict):
            continue
        for release in entry.get("release_dates") or []:
            value = _str(release.get("certification")) if isinstance(release, dict) else ""
            if value:
                by_country.setdefault(_str(entry.get("iso_3166_1")), value)
                break
    return _pick_certification(by_country)


def _tv_certification(details: JSONObject) -> str:
    block = details.get("content_ratings")
    results = block.get("results") if isinstance(block, dict) else None
    by_country = {
        _str(entry.get("iso_3166_1")): _str(entry.get("rating"))
        for entry in results or []
        if isinstance(entry, dict) and _str(entry.get("rating"))
    }
    return _pick_certification(by_country)


def _pick_certification(by_country: Mapping[str, str]) -> str:
    country = str(get_setting("metadata.certification_country"))
    return (by_country.get(country) or by_country.get("US") or "")[:16]


def _trailer(details: JSONObject) -> str:
    block = details.get("videos")
    results = block.get("results") if isinstance(block, dict) else None
    trailers = [
        item
        for item in results or []
        if isinstance(item, dict)
        and item.get("site") == "YouTube"
        and item.get("type") == "Trailer"
    ]
    trailers.sort(key=lambda item: (not item.get("official"), item.get("iso_639_1") != "en"))
    return _str(trailers[0].get("key"))[:32] if trailers else ""


def _arabic(value: object, english: str) -> str:
    """An Arabic text, or "" when the provider fell back to another language."""
    text = _str(value)
    return text if text and text != english and _ARABIC.search(text) else ""


def _str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _positive(value: object) -> int | None:
    number = _int(value)
    return number if number is not None and number > 0 else None


def _float(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


# --- Artwork --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImagePick:
    kind: str
    path: str
    language: str
    primary: bool


def _queue_images(kind: str, pk: object) -> None:
    from apps.metadata import tasks  # noqa: PLC0415

    tasks.fetch_title_images.delay(kind, str(pk))


def image_picks(details: JSONObject) -> list[ImagePick]:
    """The artwork a title gets: TMDB's primary poster and backdrop, an Arabic poster when
    there is one, and the best logo (English, then Arabic, then language-less)."""
    images = details.get("images") if isinstance(details.get("images"), dict) else {}
    posters = [p for p in (images or {}).get("posters") or [] if isinstance(p, dict)]
    logos = [p for p in (images or {}).get("logos") or [] if isinstance(p, dict)]
    languages = {
        _str(p.get("file_path")): _str(p.get("iso_639_1"))
        for p in posters + logos
        if p.get("file_path")
    }
    picks: list[ImagePick] = []
    poster = _str(details.get("poster_path"))
    if poster:
        picks.append(ImagePick(ImageKind.POSTER, poster, languages.get(poster, ""), True))
    arabic = next((_str(p.get("file_path")) for p in posters if p.get("iso_639_1") == "ar"), "")
    if arabic and arabic != poster:
        picks.append(ImagePick(ImageKind.POSTER, arabic, "ar", not poster))
    backdrop = _str(details.get("backdrop_path"))
    if backdrop:
        picks.append(ImagePick(ImageKind.BACKDROP, backdrop, "", True))
    for language in ("en", "ar", None):
        logo = next((_str(p.get("file_path")) for p in logos if p.get("iso_639_1") == language), "")
        if logo:
            picks.append(ImagePick(ImageKind.LOGO, logo, language or "", True))
            break
    return picks


def image_store() -> LocalImageStore:
    return LocalImageStore(Path(settings.DATA_ROOT))


def store_images(
    owner: Mapping[str, Any], owner_key: str, picks: Sequence[ImagePick], client: TMDBClient
) -> int:
    """Download, convert and record the picks that are not stored yet; returns how many
    were added. A failed image is logged and skipped."""
    existing = set(MediaImage.objects.filter(**owner).values_list("kind", "source_path"))
    wanted = [pick for pick in picks if (pick.kind, pick.path) not in existing]
    if not wanted:
        return 0
    store = image_store()
    added = 0
    with client.open_image_client() as http:
        for pick in wanted:
            url = image_url(pick.path, tmdb_source_size(pick.path))
            try:
                stored = fetch_and_store(
                    url,
                    client=http,
                    owner=owner_key,
                    kind=cast("ImageKindName", pick.kind),
                    store=store,
                )
            except (ImageError, ValueError) as exc:
                logger.warning("artwork skipped", extra={"owner": owner_key, "error": str(exc)})
                continue
            with transaction.atomic():
                if pick.primary:
                    MediaImage.objects.filter(**owner, kind=pick.kind).update(is_primary=False)
                MediaImage.objects.create(
                    **owner,
                    kind=pick.kind,
                    language=pick.language,
                    sizes=stored.keys(),
                    width=stored.width,
                    height=stored.height,
                    blurhash=stored.blurhash,
                    source_path=pick.path,
                    is_primary=pick.primary,
                )
            added += 1
    return added


def fetch_movie_images(movie: Movie, client: TMDBClient | None = None) -> int:
    client = client or tmdb()
    if movie.tmdb_id is None:
        return 0
    details = client.movie_details(movie.tmdb_id)
    added = store_images({"movie": movie}, f"movie/{movie.pk}", image_picks(details), client)
    if added:
        notify_catalog_changed("movie", [movie.pk])
    return added


def fetch_series_images(series: Series, client: TMDBClient | None = None) -> int:
    """Series artwork, plus posters of seasons and stills of episodes that have files."""
    client = client or tmdb()
    if series.tmdb_id is None:
        return 0
    details = client.tv_details(series.tmdb_id)
    added = store_images({"series": series}, f"series/{series.pk}", image_picks(details), client)
    season_posters = {
        _int(item.get("season_number")): _str(item.get("poster_path"))
        for item in details.get("seasons") or []
        if isinstance(item, dict)
    }
    with_files = MediaFile.objects.filter(removed_at__isnull=True)
    seasons = Season.objects.filter(series=series, episodes__files__in=with_files).distinct()
    for season in seasons:
        path = season_posters.get(season.number)
        if path:
            added += store_images(
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
        added += store_images(
            {"episode": episode},
            f"episode/{episode.pk}",
            [ImagePick(ImageKind.STILL, episode.still_path, "", True)],
            client,
        )
    if added:
        notify_catalog_changed("series", [series.pk])
    return added


# --- Refresh (SPEC §7.2 step 9) -------------------------------------------------------------------


def refresh_movie(movie: Movie, client: TMDBClient | None = None) -> Movie:
    """Fetch the movie again; locked fields stay as the admin left them."""
    client = client or tmdb()
    if movie.tmdb_id is None:
        raise ProblemError(ErrorCode.CONFLICT, "This movie has no TMDB id to refresh from.")
    return upsert_movie(movie.tmdb_id, client, refresh=True)


def refresh_series(series: Series, client: TMDBClient | None = None) -> Series:
    """Fetch the series and the seasons that have episodes again; locks are respected."""
    client = client or tmdb()
    if series.tmdb_id is None:
        raise ProblemError(ErrorCode.CONFLICT, "This series has no TMDB id to refresh from.")
    series = upsert_series(series.tmdb_id, client, refresh=True)
    for season in Season.objects.filter(series=series, episodes__isnull=False).distinct():
        try:
            ensure_season(series, season.number, client, refresh=True)
        except PlacementError:
            logger.warning("season vanished from the provider", extra={"season": season.number})
    transaction.on_commit(lambda: _queue_images("series", series.pk))
    return series


# --- The review queue (SPEC §8.3) -----------------------------------------------------------------


def _provider_problem(exc: TMDBError) -> ProblemError:
    if isinstance(exc, TMDBNotFoundError):
        return ProblemError(
            ErrorCode.VALIDATION_ERROR,
            "TMDB has no title with this id.",
            field_errors={"tmdb_id": ["Not found on TMDB."]},
        )
    return ProblemError(ErrorCode.PROVIDER_UNAVAILABLE, "TMDB is unavailable; try again shortly.")


def resolve_review(  # noqa: PLR0913 (everything after tmdb_id is keyword-only)
    review: MatchReview,
    tmdb_id: int,
    *,
    kind: str | None = None,
    actor: User | None,
    ip: str | None,
    client: TMDBClient | None = None,
) -> MatchReview:
    """Match the review's file to the TMDB movie or series the admin chose."""
    if review.status != ReviewStatus.OPEN:
        raise ProblemError(ErrorCode.CONFLICT, "This review was already decided.")
    client = client or tmdb()
    kind = kind or review.kind
    file = review.media_file
    parsed = ParseResult.from_json(review.parse_result or file.parse_result)
    try:
        if kind == ReviewKind.MOVIE:
            accept_movie(file, tmdb_id, 1.0, client, actor=actor)
        else:
            accept_episode(file, tmdb_id, parsed, 1.0, client, actor=actor)
    except PlacementError as exc:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR, str(exc), field_errors={"tmdb_id": [str(exc)]}
        ) from None
    except TMDBError as exc:
        raise _provider_problem(exc) from None
    review.refresh_from_db()
    with transaction.atomic():
        review.kind = kind
        review.status = ReviewStatus.RESOLVED
        review.chosen_provider_id = tmdb_id
        review.decided_by = actor
        review.decided_at = timezone.now()
        review.save()
        audit.record(
            "review.resolve",
            actor=actor,
            target=review,
            after={"kind": kind, "tmdb_id": tmdb_id, "file": str(file.pk)},
            ip=ip,
        )
    return review


def skip_review(review: MatchReview, *, actor: User | None, ip: str | None) -> MatchReview:
    """Set a review aside: the file stays unmatched until it is matched again."""
    if review.status != ReviewStatus.OPEN:
        raise ProblemError(ErrorCode.CONFLICT, "This review was already decided.")
    with transaction.atomic():
        review.status = ReviewStatus.SKIPPED
        review.decided_by = actor
        review.decided_at = timezone.now()
        review.save()
        audit.record("review.skip", actor=actor, target=review, ip=ip)
    return review


def search(
    kind: str, query: str, year: int | None = None, client: TMDBClient | None = None
) -> list[dict[str, Any]]:
    """TMDB search for manual matching: candidates as the review queue shows them."""
    client = client or tmdb()
    try:
        if kind == ReviewKind.MOVIE:
            results = client.search_movie(query, year=year).get("results") or []
        else:
            results = client.search_tv(query, first_air_date_year=year).get("results") or []
    except TMDBError as exc:
        raise _provider_problem(exc) from None
    found = []
    for item in results[: SEARCH_LIMIT * 2]:
        if not isinstance(item, dict):
            continue
        candidate = Candidate.from_tmdb(item, "movie" if kind == ReviewKind.MOVIE else "tv")
        found.append(
            {
                "provider": "tmdb",
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


def poster_preview_url(poster_path: str | None, client: TMDBClient | None = None) -> str | None:
    """A TMDB thumbnail URL for a candidate; None in fixture mode (synthetic paths)."""
    if not poster_path:
        return None
    client = client or tmdb()
    return None if client.fixture_mode else image_url(poster_path, "w185")


def match_file_task_body(file_id: str, *, client_factory: Callable[[], TMDBClient] = tmdb) -> None:
    """What the Celery task runs: match a file that is waiting for it."""
    file = MediaFile.objects.select_related("library").filter(pk=file_id).first()
    if file is None or file.removed_at is not None or file.state != FileState.MATCHING:
        return
    match_file(file, client_factory())
    refresh_status_of_files([file.pk])
