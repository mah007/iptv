"""The catalogue lookup behind playback (SPEC §7.4): a movie or an episode as a
`PlayableTitle` for `apps.playback.services.start_playback`.

Used by the Xtream play URLs (`/movie|/series/{u}/{p}/{xc_id}.{ext}`) and the portal
API. A title is playable when it is `ready` (an episode: its series is); its
renditions are the ready progressive renditions of its active files (ADR-0010). An
episode of a ready series whose own files are still being prepared has no
renditions, which `start_playback` answers with TITLE_PREPARING.

Each lookup takes at most three queries: the title, its categories, its renditions.
Renditions carry the asset key (`Rendition.storage_key`), never a storage path.
"""

from typing import Any
from uuid import UUID

from django.db.models import QuerySet

from apps.catalog.models import Category, Episode, Movie, TitleStatus
from apps.media.models import PLAYABLE_KINDS, Rendition, RenditionKind, RenditionStatus
from apps.playback.models import TitleKind
from apps.playback.services import PlayableRendition, PlayableTitle
from apps.playback.services import RenditionKind as Kind

_KINDS = {RenditionKind.COMPAT_MP4: Kind.COMPAT, RenditionKind.SOURCE: Kind.SOURCE}


def playable_title(kind: TitleKind, xc_id: int) -> PlayableTitle | None:
    """The ready movie or episode with this Xtream id; None if unknown or not ready."""
    return _lookup(kind, {"xc_id": xc_id})


def playable_title_by_id(kind: TitleKind, title_id: UUID) -> PlayableTitle | None:
    """`playable_title` by primary key (the portal API's ids)."""
    return _lookup(kind, {"pk": title_id})


def _lookup(kind: TitleKind, where: dict[str, Any]) -> PlayableTitle | None:
    if kind == TitleKind.MOVIE:
        return _movie(where)
    if kind == TitleKind.EPISODE:
        return _episode(where)
    return None


def _movie(where: dict[str, Any]) -> PlayableTitle | None:
    movie = Movie.objects.filter(status=TitleStatus.READY, **where).first()
    if movie is None:
        return None
    categories = Category.objects.filter(movies=movie)
    renditions = _renditions(Rendition.objects.filter(media_file__movie=movie))
    return _build(
        TitleKind.MOVIE,
        movie.pk,
        categories,
        renditions,
        runtime_min=movie.runtime_min,
        license_expires_at=movie.license_expires_at,
        name=str(movie.title),
    )


def _episode(where: dict[str, Any]) -> PlayableTitle | None:
    episode = (
        Episode.objects.select_related("season__series")
        .filter(season__series__status=TitleStatus.READY, **where)
        .first()
    )
    if episode is None:
        return None
    series = episode.season.series
    categories = Category.objects.filter(series=series)
    renditions = _renditions(Rendition.objects.filter(media_file__episodes=episode))
    name = f"{series.title} S{episode.season.number:02d}E{episode.number:02d}"
    if episode.title:
        name = f"{name} {episode.title}"
    return _build(
        TitleKind.EPISODE,
        episode.pk,
        categories,
        renditions,
        runtime_min=episode.runtime_min or series.episode_run_time,
        license_expires_at=series.license_expires_at,
        name=name,
    )


def _renditions(rows: QuerySet[Rendition]) -> list[dict[str, Any]]:
    return list(
        rows.filter(
            status=RenditionStatus.READY,
            kind__in=PLAYABLE_KINDS,
            media_file__removed_at__isnull=True,
        )
        .order_by("-media_file__is_primary", "-height", "kind")
        .values("storage_key", "kind", "height", "container", "duration_s")
    )


def _build(  # noqa: PLR0913 (the title's playback facts)
    kind: TitleKind,
    title_id: UUID,
    categories: QuerySet[Category],
    renditions: list[dict[str, Any]],
    *,
    runtime_min: int | None,
    license_expires_at: Any,
    name: str,
) -> PlayableTitle:
    category_rows = list(categories.values_list("id", "is_adult"))
    playable: list[PlayableRendition] = []
    seen: set[tuple[str, str]] = set()
    for row in renditions:
        identity = (row["storage_key"], row["kind"])
        if identity in seen:
            continue
        seen.add(identity)
        playable.append(
            PlayableRendition(
                storage_key=row["storage_key"],
                kind=_KINDS[RenditionKind(row["kind"])],
                height=row["height"] or 0,
                container=row["container"] or "mp4",
            )
        )
    durations = [row["duration_s"] for row in renditions if row["duration_s"]]
    runtime_s = round(max(durations)) if durations else (runtime_min or 0) * 60
    return PlayableTitle(
        kind=kind,
        id=title_id,
        renditions=tuple(playable),
        category_ids=frozenset(pk for pk, _adult in category_rows),
        adult=any(adult for _pk, adult in category_rows),
        runtime_s=runtime_s,
        license_expires_at=license_expires_at,
        name=name,
    )
