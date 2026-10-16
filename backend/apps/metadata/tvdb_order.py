"""TheTVDB's DVD or absolute order for series matched on TMDB (ADR-0024).

TMDB numbers episodes in aired order. Some libraries are named after the DVD sets
(`S01E08` for what aired as `S02E01`) or with absolute numbers (anime: `Show - 012`).
When a TMDB series knows its TheTVDB id (TMDB's `external_ids`) and the admin sets its
episode order to `tvdb_dvd` or `tvdb_absolute` (the series page), a file's numbers are
read in that order and mapped to TMDB's through TheTVDB's episode ids:

    file S01E08 (DVD) -> TheTVDB episode 3490008 -> aired S02E01 -> TMDB S02E01

The aired numbers are TheTVDB's `official` order, which TMDB follows for nearly every
series. For the absolute order, a name's absolute number (`- 012`) or its episode
number in season 1 (TheTVDB puts every absolute episode in season 1) is used; other
seasons keep their numbers. TheTVDB's answers are cached like every other TheTVDB call.
Changing the order re-places the series' files (`replace_series_files`).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from apps.catalog.models import EpisodeOrdering, MediaFile, MetadataSource, Series
from apps.library.parsing import ParseResult
from apps.metadata import tvdb_series
from apps.metadata.services import PlacementError
from apps.metadata.tvdb import SeasonType, TVDBClient, TVDBError

logger = logging.getLogger(__name__)

#: The TheTVDB orders a TMDB series may follow, and their TheTVDB season types.
ORDERS: Final[dict[str, SeasonType]] = {
    EpisodeOrdering.TVDB_DVD: "dvd",
    EpisodeOrdering.TVDB_ABSOLUTE: "absolute",
}


@dataclass(frozen=True, slots=True)
class Placed:
    """Where a file's episodes are in TMDB's (aired) numbering."""

    numbers: tuple[tuple[int, int], ...]  # (season, episode), in the file's order


def applies(series: Series) -> bool:
    """Whether this TMDB series reads file numbers in a TheTVDB order."""
    return (
        series.metadata_source != MetadataSource.TVDB
        and series.tvdb_id is not None
        and series.episode_ordering in ORDERS
    )


def _wanted(order: SeasonType, parsed: ParseResult) -> list[tuple[int, int]] | None:
    """The (season, number) keys of the file in TheTVDB's `order`, or None when the name
    has no numbers that order uses."""
    if order == "absolute":
        if parsed.absolute_episode is not None:
            return [(1, parsed.absolute_episode)]
        if parsed.season in (None, 1) and parsed.episodes:
            return [(1, number) for number in parsed.episodes]
        return None
    if parsed.season is None or not parsed.episodes:
        return None
    return [(parsed.season, number) for number in parsed.episodes]


def _keys(episodes: Sequence[dict[str, Any]]) -> dict[tuple[int, int], int]:
    keys: dict[tuple[int, int], int] = {}
    for item in episodes:
        season, number, ident = item.get("seasonNumber"), item.get("number"), item.get("id")
        if isinstance(season, int) and isinstance(number, int) and isinstance(ident, int):
            keys.setdefault((season, number), ident)
    return keys


def aired_numbers(
    series: Series, parsed: ParseResult, client: TVDBClient | None = None
) -> Placed | None:
    """The TMDB (aired) numbers of a file named in the series' TheTVDB order; None when
    the series doesn't use one or the name has no numbers that order uses (the file's own
    numbers then stand). PlacementError when TheTVDB can't be asked or doesn't know one
    of the file's episodes."""
    if not applies(series):
        return None
    order = ORDERS[series.episode_ordering]
    wanted = _wanted(order, parsed)
    if wanted is None:
        return None
    client = client or tvdb_series.tvdb()
    if client is None or series.tvdb_id is None:
        raise PlacementError(
            "tvdb_unavailable",
            f"{series.title} follows TheTVDB's {order} order: save a TheTVDB key in "
            "Settings → Integrations to place its files.",
        )
    try:
        chosen = _keys(client.series_episodes(series.tvdb_id, season_type=order))
        official = client.series_episodes(series.tvdb_id, season_type="official")
    except TVDBError as exc:
        logger.warning("TheTVDB unavailable for the %s order of series %s", order, series.pk)
        raise PlacementError(
            "tvdb_unavailable", f"TheTVDB could not be asked for the {order} order."
        ) from exc
    aired = {ident: key for key, ident in _keys(official).items()}
    numbers: list[tuple[int, int]] = []
    for key in wanted:
        ident = chosen.get(key)
        place = aired.get(ident) if ident is not None else None
        if place is None:
            raise PlacementError(
                "episode_not_found",
                f"TheTVDB's {order} order of {series.title} has no season {key[0]} "
                f"episode {key[1]}.",
            )
        numbers.append(place)
    return Placed(numbers=tuple(numbers))


def replace_series_files(series_id: str, client: TVDBClient | None = None) -> dict[str, int]:
    """Place the series' files again after its order changed (a task queues this).

    Each file is placed from its own parsed name with the series' current order, keeping
    its confidence. Files that can't be placed stay where they were (and are counted)."""
    from apps.metadata import services  # noqa: PLC0415 (services imports this module)

    series = Series.objects.filter(pk=series_id).first()
    if series is None or series.tmdb_id is None:
        return {"placed": 0, "kept": 0}
    files = list(
        MediaFile.objects.filter(episodes__season__series=series, removed_at__isnull=True)
        .distinct()
        .order_by("storage_key")
    )
    placed = kept = 0
    for file in files:
        parsed = ParseResult.from_json(file.parse_result)
        try:
            services.accept_episode(
                file, series.tmdb_id, parsed, file.match_confidence or 1.0, tvdb_client=client
            )
            placed += 1
        except PlacementError as exc:
            kept += 1
            logger.info("file %s kept its episodes: %s", file.pk, exc)
    return {"placed": placed, "kept": kept}
