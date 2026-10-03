"""get.php (M3U, compat/m3u.md) and xmltv.php (compat/xmltv.md), as pure functions.

The playlist lists exactly what player_api.php shows the account: entries are
built from the same payloads (order, names and visible categories included),
then rendered with the credentials the playlist was fetched with. Entries carry
no credentials, so they can be cached per scope and locale like the payloads.

Never log a playlist body or URL: every play URL carries the device credentials.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote, urlencode
from xml.sax.saxutils import quoteattr

from apps.xtream_api import payloads
from apps.xtream_api.dto import EpisodeRef

type EntryKind = Literal["live", "movie", "series"]
type LiveExtension = Literal["ts", "m3u8"]

CONTENT_TYPE = "audio/x-mpegurl; charset=utf-8"
GUIDE_CONTENT_TYPE = "application/xml; charset=utf-8"
#: output= → extension of live URLs; movies and episodes always end in .mp4, and
#: live is never MP4. "hls" is an alias some apps send for m3u8.
LIVE_EXTENSIONS: dict[str, LiveExtension] = {"ts": "ts", "m3u8": "m3u8", "hls": "m3u8", "mp4": "ts"}


@dataclass(frozen=True, slots=True)
class Entry:
    kind: EntryKind
    xc_id: int
    name: str
    logo: str
    group: str
    tvg_id: str = ""

    def row(self) -> list[object]:
        """A compact JSON-able form for the response cache."""
        return [self.kind, self.xc_id, self.name, self.logo, self.group, self.tvg_id]

    @classmethod
    def from_row(cls, row: Sequence[object]) -> "Entry":
        kind, xc_id, name, logo, group, tvg_id = row
        return cls(
            kind=_KINDS[str(kind)],
            xc_id=_int(xc_id),
            name=str(name),
            logo=str(logo),
            group=str(group),
            tvg_id=str(tvg_id),
        )


_KINDS: dict[str, EntryKind] = {"live": "live", "movie": "movie", "series": "series"}


def live_extension(output: str) -> LiveExtension:
    return LIVE_EXTENSIONS.get(output.strip().lower(), "ts")


def entries(
    movies: Sequence[payloads.Payload],
    movie_categories: Sequence[payloads.Payload],
    series: Sequence[payloads.Payload],
    series_categories: Sequence[payloads.Payload],
    episodes: Iterable[EpisodeRef],
) -> list[Entry]:
    """Movies, then episodes by series, season (S00 first) and episode number.

    `movies` and `series` are get_vod_streams and get_series payloads, so the
    playlist inherits their order, names and category visibility. Episodes of
    series those lists don't show are left out.
    """
    movie_groups = _names(movie_categories)
    series_groups = _names(series_categories)
    result = [
        Entry(
            kind="movie",
            xc_id=_int(movie["stream_id"]),
            name=str(movie["name"]),
            logo=str(movie["stream_icon"]),
            group=movie_groups[_int(movie["category_id"])],
        )
        for movie in movies
    ]
    by_series: dict[int, list[EpisodeRef]] = {}
    for episode in episodes:
        by_series.setdefault(episode.series_xc_id, []).append(episode)
    for item in series:
        series_name = str(item["name"])
        for season, items in payloads.playable_seasons(
            by_series.get(_int(item["series_id"]), ())
        ).items():
            result.extend(
                Entry(
                    kind="series",
                    xc_id=episode.xc_id,
                    name=f"{series_name} S{season:02d}E{episode.number:02d}",
                    logo=str(item["cover"]),
                    group=series_groups[_int(item["category_id"])],
                )
                for episode in items
            )
    return result


def render(
    items: Iterable[Entry], *, origin: str, username: str, password: str, live_ext: LiveExtension
) -> bytes:
    """The m3u_plus playlist: UTF-8, LF line endings, two lines per entry."""
    user, secret = quote(username, safe=""), quote(password, safe="")
    guide = f"{origin}/xmltv.php?{urlencode({'username': username, 'password': password})}"
    lines = [f'#EXTM3U url-tvg="{guide}"']
    for entry in items:
        title = _text(entry.name) or str(entry.xc_id)
        lines.append(
            f'#EXTINF:-1 tvg-id="{_text(entry.tvg_id)}" tvg-name="{title}" '
            f'tvg-logo="{payloads.image(entry.logo)}" group-title="{_text(entry.group)}",{title}'
        )
        extension = live_ext if entry.kind == "live" else payloads.CONTAINER_EXTENSION
        lines.append(f"{origin}/{entry.kind}/{user}/{secret}/{entry.xc_id}.{extension}")
    return ("\n".join(lines) + "\n").encode()


def empty_guide(generator: str) -> bytes:
    """A valid XMLTV document without channels (live TV and its guide arrive in M12)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<tv generator-info-name={quoteattr(generator)}></tv>\n"
    ).encode()


def _text(value: str) -> str:
    """M3U attribute values and names never hold quotes or line breaks."""
    return " ".join(value.replace('"', "'").split())


def _names(categories: Sequence[payloads.Payload]) -> dict[int, str]:
    return {
        _int(category["category_id"]): str(category["category_name"]) for category in categories
    }


def _int(value: object) -> int:
    return int(str(value))
