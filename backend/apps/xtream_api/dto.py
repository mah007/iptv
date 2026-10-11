"""What the Xtream layer reads from the catalog: small immutable records (SPEC §7.5).

A `CatalogSource` (source.py) returns these; the pure builders in payloads.py and
playlist.py turn them into PHP-typed JSON and M3U. They carry both catalog
languages where text is localised, so the builders pick the user's locale and the
cache key, not the source, decides which language a response is in.

Images are absolute URLs (the source resolves storage keys); builders drop any
that IPTV apps cannot decode. Category lists hold category `xc_id`s, primary first.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Literal

type Locale = Literal["en", "ar"]
type CategoryKind = Literal["vod", "series", "live"]


@dataclass(frozen=True, slots=True)
class Text:
    """A text in both catalog languages; either may be empty."""

    en: str = ""
    ar: str = ""

    def pick(self, locale: Locale) -> str:
        """The text in `locale`, else in the other language, else ""."""
        return self.only(locale) or self.only("en" if locale == "ar" else "ar")

    def only(self, locale: Locale) -> str:
        """The text in `locale`, without falling back to the other language."""
        return (self.ar if locale == "ar" else self.en).strip()


@dataclass(frozen=True, slots=True)
class CategoryItem:
    xc_id: int
    name: Text
    sort: int = 0


@dataclass(frozen=True, slots=True)
class VideoStream:
    """The delivered video stream (the compat rendition, or the direct-play source)."""

    codec: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class AudioStream:
    """The first delivered audio track; codec "" and 0 channels when there is none."""

    codec: str = ""
    channels: int = 0


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """What a player receives when it plays the title."""

    duration_secs: int
    bitrate_kbps: int
    video: VideoStream
    audio: AudioStream = AudioStream()


@dataclass(frozen=True, slots=True)
class MovieSummary:
    """A playable movie as get_vod_streams lists it."""

    xc_id: int
    title: Text
    added: datetime
    category_ids: tuple[int, ...]
    poster: str = ""
    rating: float | None = None  # 0-10; None or 0 when unrated
    tmdb_id: int | None = None
    is_adult: bool = False


@dataclass(frozen=True, slots=True)
class MovieDetail:
    """get_vod_info: the summary plus details and the delivered media."""

    movie: MovieSummary
    media: MediaInfo
    original_title: str = ""
    poster_large: str = ""
    backdrops: tuple[str, ...] = ()
    overview: Text = Text()
    cast: tuple[str, ...] = ()
    directors: tuple[str, ...] = ()
    genres: tuple[Text, ...] = ()
    release_date: date | None = None
    year: int | None = None
    countries: tuple[str, ...] = ()
    imdb_id: str = ""
    trailer_key: str = ""  # YouTube video id
    certification: str = ""


@dataclass(frozen=True, slots=True)
class SeriesSummary:
    """A series with at least one playable episode, as get_series lists it."""

    xc_id: int
    title: Text
    last_modified: datetime  # when an episode was last added or changed
    category_ids: tuple[int, ...]
    poster: str = ""
    poster_large: str = ""
    backdrops: tuple[str, ...] = ()
    overview: Text = Text()
    cast: tuple[str, ...] = ()
    creators: tuple[str, ...] = ()
    genres: tuple[Text, ...] = ()
    first_air_date: date | None = None
    rating: float | None = None
    trailer_key: str = ""
    episode_run_time: int | None = None  # minutes
    tmdb_id: int | None = None


@dataclass(frozen=True, slots=True)
class SeasonItem:
    number: int
    id: int  # the TMDB season id when known, else any stable integer
    name: Text = Text()
    poster: str = ""
    poster_large: str = ""
    air_date: date | None = None
    overview: Text = Text()


@dataclass(frozen=True, slots=True)
class EpisodeItem:
    """A playable episode in get_series_info. Specials are season 0."""

    xc_id: int
    season: int
    number: int
    added: datetime
    media: MediaInfo
    title: Text = Text()
    still: str = ""
    overview: Text = Text()
    air_date: date | None = None
    rating: float | None = None


@dataclass(frozen=True, slots=True)
class SeriesDetail:
    """get_series_info: seasons may include ones without playable episodes; the
    builder lists only seasons that have some."""

    series: SeriesSummary
    seasons: tuple[SeasonItem, ...]
    episodes: tuple[EpisodeItem, ...]


@dataclass(frozen=True, slots=True)
class EpisodeRef:
    """A playable episode for the M3U playlist."""

    xc_id: int
    series_xc_id: int
    season: int
    number: int


class TitleKind(StrEnum):
    MOVIE = "movie"
    EPISODE = "episode"
    LIVE = "live"
    CATCHUP = "catchup"  # a live channel's archive (timeshift)


@dataclass(frozen=True, slots=True)
class TitleRef:
    """The title behind a play URL: its kind, Xtream id and primary key.

    `output` is a live channel's default extension (`ts` or `m3u8`) for the
    extension-less URL `/{u}/{p}/{id}`; "" for movies and episodes.
    """

    kind: TitleKind
    xc_id: int
    id: str
    output: str = ""


# --- Live TV (M12) ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelItem:
    """A live channel as get_live_streams lists it."""

    xc_id: int
    name: Text
    added: datetime
    category_ids: tuple[int, ...]
    sort: int = 0
    logo: str = ""
    epg_channel_id: str = ""
    catchup_days: int = 0  # 0: no catch-up
    is_adult: bool = False


@dataclass(frozen=True, slots=True)
class Programme:
    """One guide entry. `listing_id` is stable across imports; `epg_id` names the source."""

    listing_id: int
    epg_id: int
    start: datetime
    stop: datetime
    title: Text
    description: Text = Text()
    lang: str = ""  # ISO 639 code of the English-side title, "" when unknown


@dataclass(frozen=True, slots=True)
class ChannelGuide:
    """A visible channel and its programmes in a window, start order.

    `archive_from`: the first instant the catch-up archive holds (None: nothing yet).
    """

    channel: ChannelItem
    programmes: tuple[Programme, ...]
    archive_from: datetime | None = None


@dataclass(frozen=True, slots=True)
class TimeshiftWindow:
    """A catch-up request: `/timeshift/{u}/{p}/{minutes}/{start}/{id}.{ext}` (UTC)."""

    start: datetime
    minutes: int
