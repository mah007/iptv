"""Xtream actions: catalog JSON, the login payload, the playlist and play starts (SPEC §7.5).

Views parse HTTP and authenticate; these functions decide what an account gets.
Catalog responses come from the response cache (cache.py) or are built from the
catalog source (source.py) with the pure builders (payloads.py, playlist.py).
Accounts that are not "Active" get valid, empty catalog responses.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, cast

import orjson
import structlog
from django.utils import timezone

from apps.core.services import get_setting
from apps.xtream_api import cache, payloads, playlist
from apps.xtream_api.auth import XtreamAccount, server_info
from apps.xtream_api.dto import (
    CategoryItem,
    CategoryKind,
    EpisodeRef,
    TimeshiftWindow,
    TitleKind,
    TitleRef,
)
from apps.xtream_api.live_source import live_source
from apps.xtream_api.playback import PlayOutcome, PlayRefused, PlayRequest, playback_starter
from apps.xtream_api.source import catalog_source

logger = structlog.get_logger(__name__)

_ID = re.compile(r"[1-9][0-9]{0,15}")

type Params = Mapping[str, str]


@dataclass(frozen=True, slots=True)
class JsonResult:
    body: bytes
    status: int = 200


type Handler = Callable[[XtreamAccount, Params], JsonResult]


def parse_id(value: str | None) -> int | None:
    """A positive decimal id, or None for anything else."""
    text = (value or "").strip()
    return int(text) if _ID.fullmatch(text) else None


# --- Login --------------------------------------------------------------------------------


def login_body(
    account: XtreamAccount, *, username: str, password: str, now: datetime | None = None
) -> bytes:
    info = payloads.AccountInfo(
        username=username,
        password=password,
        status=account.status,
        expires_at=account.expires_at,
        created_at=account.user.created_at,
        max_connections=account.max_connections,
        active_connections=playback_starter().active_streams(account.user_id),
    )
    return orjson.dumps(payloads.login(info, server_info(), now or timezone.now()))


# --- Catalog actions ----------------------------------------------------------------------


def catalog_action(account: XtreamAccount, action: str, params: Params) -> JsonResult | None:
    """The response to a catalog action; None for anything else (the login payload)."""
    handler = _HANDLERS.get(action)
    return handler(account, params) if handler is not None else None


def _allowed(account: XtreamAccount, kind: Literal["vod", "series", "live"]) -> bool:
    scope = account.scope
    if not account.active:
        return False
    if kind == "live":
        return scope.allow_live
    return scope.allow_movies if kind == "vod" else scope.allow_series


def _source_categories(account: XtreamAccount, kind: CategoryKind) -> list[CategoryItem]:
    return list(catalog_source().categories(account.scope, kind))


def _categories_body(account: XtreamAccount, kind: Literal["vod", "series"]) -> bytes:
    if not _allowed(account, kind):
        return payloads.EMPTY_LIST
    body = cache.cached(
        account.scope,
        account.locale,
        f"get_{kind}_categories",
        lambda: payloads.categories(_source_categories(account, kind), account.locale),
    )
    return body or payloads.EMPTY_LIST


def _category_list(kind: Literal["vod", "series"]) -> Handler:
    def handle(account: XtreamAccount, params: Params) -> JsonResult:
        return JsonResult(_categories_body(account, kind))

    return handle


def _visible_category(
    account: XtreamAccount, kind: Literal["vod", "series"], value: str
) -> int | None:
    """The requested category id if the account can see it, else None."""
    category_id = parse_id(value)
    if category_id is None:
        return None
    listed = cast("list[payloads.Payload]", orjson.loads(_categories_body(account, kind)))
    return category_id if category_id in payloads.ids_of(listed) else None


def _stream_list(kind: Literal["vod", "series"]) -> Handler:
    action = "get_vod_streams" if kind == "vod" else "get_series"

    def build(account: XtreamAccount, category_id: int | None) -> list[payloads.Payload]:
        source, scope, locale = catalog_source(), account.scope, account.locale
        categories = _source_categories(account, kind)
        if kind == "vod":
            return payloads.vod_streams(source.movies(scope), categories, locale, category_id)
        return payloads.series_list(source.series_list(scope), categories, locale, category_id)

    def handle(account: XtreamAccount, params: Params) -> JsonResult:
        if not _allowed(account, kind):
            return JsonResult(payloads.EMPTY_LIST)
        requested = params.get("category_id", "").strip()
        category_id = _visible_category(account, kind, requested) if requested else None
        if requested and category_id is None:  # unknown, hidden or malformed: nothing in it
            return JsonResult(payloads.EMPTY_LIST)
        body = cache.cached(
            account.scope,
            account.locale,
            action,
            lambda: build(account, category_id),
            extra=str(category_id or ""),
        )
        return JsonResult(body or payloads.EMPTY_LIST)

    return handle


def _vod_info(account: XtreamAccount, params: Params) -> JsonResult:
    vod_id = parse_id(params.get("vod_id"))
    if vod_id is None or not _allowed(account, "vod"):
        return JsonResult(payloads.NOT_FOUND, 404)

    def build() -> payloads.Payload | None:
        detail = catalog_source().movie(account.scope, vod_id)
        if detail is None:
            return None
        return payloads.vod_info(detail, _source_categories(account, "vod"), account.locale)

    body = cache.cached(account.scope, account.locale, "get_vod_info", build, extra=str(vod_id))
    return JsonResult(body) if body is not None else JsonResult(payloads.NOT_FOUND, 404)


def _series_info(account: XtreamAccount, params: Params) -> JsonResult:
    series_id = parse_id(params.get("series_id"))
    if series_id is None or not _allowed(account, "series"):
        return JsonResult(payloads.NOT_FOUND, 404)

    def build() -> payloads.Payload | None:
        detail = catalog_source().series(account.scope, series_id)
        if detail is None:
            return None
        return payloads.series_info(detail, _source_categories(account, "series"), account.locale)

    body = cache.cached(
        account.scope, account.locale, "get_series_info", build, extra=str(series_id)
    )
    return JsonResult(body) if body is not None else JsonResult(payloads.NOT_FOUND, 404)


# --- Live TV and its guide (M12) ---------------------------------------------------------

#: EPG answers change as programmes start and end: cached per minute, briefly.
EPG_CACHE_TTL_S = 90


def _live_categories_body(account: XtreamAccount) -> bytes:
    if not _allowed(account, "live"):
        return payloads.EMPTY_LIST
    body = cache.cached(
        account.scope,
        account.locale,
        "get_live_categories",
        lambda: payloads.categories(live_source().categories(account.scope), account.locale),
    )
    return body or payloads.EMPTY_LIST


def _live_categories(account: XtreamAccount, params: Params) -> JsonResult:
    return JsonResult(_live_categories_body(account))


def _live_streams(account: XtreamAccount, params: Params) -> JsonResult:
    if not _allowed(account, "live"):
        return JsonResult(payloads.EMPTY_LIST)
    requested = params.get("category_id", "").strip()
    category_id = parse_id(requested) if requested else None
    if requested:
        listed = cast("list[payloads.Payload]", orjson.loads(_live_categories_body(account)))
        if category_id is None or category_id not in payloads.ids_of(listed):
            return JsonResult(payloads.EMPTY_LIST)

    def build() -> list[payloads.Payload]:
        source, scope = live_source(), account.scope
        return payloads.live_streams(
            source.channels(scope), source.categories(scope), account.locale, category_id
        )

    body = cache.cached(
        account.scope, account.locale, "get_live_streams", build, extra=str(category_id or "")
    )
    return JsonResult(body or payloads.EMPTY_LIST)


def _epg_limit(value: str | None) -> int:
    parsed = parse_id(value)
    if parsed is None:
        return payloads.SHORT_EPG_LIMIT
    return min(parsed, payloads.MAX_EPG_LIMIT)


def _epg(*, short: bool) -> Handler:
    action = "get_short_epg" if short else "get_simple_data_table"

    def handle(account: XtreamAccount, params: Params) -> JsonResult:
        stream_id = parse_id(params.get("stream_id"))
        if stream_id is None or not _allowed(account, "live"):
            return JsonResult(payloads.EMPTY_EPG)
        limit = _epg_limit(params.get("limit")) if short else None
        now = timezone.now()
        future = timedelta(days=int(cast("int", get_setting("live.epg_future_days"))))
        if short:
            start = now
        else:
            start = now - timedelta(
                days=max(1, int(cast("int", get_setting("live.catchup_max_days"))))
            )

        def build() -> payloads.Payload:
            guide = live_source().guide(account.scope, stream_id, start=start, end=now + future)
            return payloads.epg_listings(guide, account.locale, now, limit=limit)

        body = cache.cached(
            account.scope,
            account.locale,
            action,
            build,
            extra=f"{stream_id}:{limit or ''}:{int(now.timestamp()) // 60}",
            ttl=EPG_CACHE_TTL_S,
        )
        return JsonResult(body or payloads.EMPTY_EPG)

    return handle


_HANDLERS: dict[str, Handler] = {
    "get_vod_categories": _category_list("vod"),
    "get_series_categories": _category_list("series"),
    "get_vod_streams": _stream_list("vod"),
    "get_vod_info": _vod_info,
    "get_series": _stream_list("series"),
    "get_series_info": _series_info,
    "get_live_categories": _live_categories,
    "get_live_streams": _live_streams,
    "get_short_epg": _epg(short=True),
    "get_simple_data_table": _epg(short=False),
}


# --- get.php and xmltv.php ------------------------------------------------------------------


def playlist_body(account: XtreamAccount, *, username: str, password: str, output: str) -> bytes:
    """The m3u_plus playlist (compat/m3u.md): what player_api.php shows the account.

    Live channels always; movies and episodes while `features.include_vod_in_m3u` is on.
    """
    entries: list[playlist.Entry] = []
    if account.active:
        include_vod = bool(get_setting("features.include_vod_in_m3u"))
        body = cache.cached(
            account.scope,
            account.locale,
            "m3u",
            lambda: _playlist_rows(account, include_vod=include_vod),
            extra="vod" if include_vod else "live",
        )
        rows = cast("list[list[object]]", orjson.loads(body or payloads.EMPTY_LIST))
        entries = [playlist.Entry.from_row(row) for row in rows]
    return playlist.render(
        entries,
        origin=server_info().origin,
        username=username,
        password=password,
        live_ext=playlist.live_extension(output),
    )


def _playlist_rows(account: XtreamAccount, *, include_vod: bool = True) -> list[list[object]]:
    source, scope, locale = catalog_source(), account.scope, account.locale
    live: list[payloads.Payload] = []
    live_categories: list[payloads.Payload] = []
    if scope.allow_live:
        channels = live_source()
        categories = list(channels.categories(scope))
        live = payloads.live_streams(channels.channels(scope), categories, locale)
        live_categories = payloads.categories(categories, locale)
    movies: list[payloads.Payload] = []
    movie_categories: list[payloads.Payload] = []
    if include_vod and scope.allow_movies:
        categories = _source_categories(account, "vod")
        movies = payloads.vod_streams(source.movies(scope), categories, locale)
        movie_categories = payloads.categories(categories, locale)
    series: list[payloads.Payload] = []
    series_categories: list[payloads.Payload] = []
    episodes: tuple[EpisodeRef, ...] = ()
    if include_vod and scope.allow_series:
        categories = _source_categories(account, "series")
        series = payloads.series_list(source.series_list(scope), categories, locale)
        series_categories = payloads.categories(categories, locale)
        episodes = tuple(source.episodes(scope))
    entries = playlist.entries(
        movies, movie_categories, series, series_categories, episodes, live, live_categories
    )
    return [entry.row() for entry in entries]


#: xmltv.php is large and changes with imports (which retire the cache) and the clock.
GUIDE_CACHE_TTL_S = 600


def guide_body(account: XtreamAccount) -> bytes:
    """xmltv.php: the account's channels and their programmes (compat/xmltv.md)."""
    generator = str(get_setting("branding.service_name_en"))
    if not _allowed(account, "live"):
        return playlist.empty_guide(generator)
    now = timezone.now()
    body = cache.cached_bytes(
        account.scope,
        account.locale,
        "xmltv",
        lambda: playlist.guide(live_source().guides(account.scope, now=now), generator),
        ttl=GUIDE_CACHE_TTL_S,
    )
    return body or playlist.empty_guide(generator)


# --- Play URLs ------------------------------------------------------------------------------


def start_play(  # noqa: PLR0913 (keyword-only request context)
    account: XtreamAccount,
    *,
    kind: TitleKind,
    xc_id: int,
    extension: str,
    ip: str | None,
    user_agent: str = "",
) -> PlayOutcome:
    """Resolve the title and ask the playback starter for a signed edge URL."""
    title = catalog_source().title(kind, xc_id)
    return _start(account, title, kind, xc_id, extension.lower(), None, ip, user_agent)


def start_live(  # noqa: PLR0913 (keyword-only request context)
    account: XtreamAccount,
    *,
    xc_id: int,
    extension: str | None,
    window: TimeshiftWindow | None = None,
    ip: str | None,
    user_agent: str = "",
) -> PlayOutcome:
    """A live channel (`/live/...`, `/{u}/{p}/{id}`) or its catch-up (`/timeshift/...`).

    Without an extension, the channel's own default (`output`) applies.
    """
    channel = live_source().channel(xc_id)
    kind = TitleKind.CATCHUP if window is not None else TitleKind.LIVE
    title = None
    if channel is not None:
        title = TitleRef(kind=kind, xc_id=channel.xc_id, id=channel.id, output=channel.output)
    ext = (extension or (channel.output if channel else "") or "ts").lower()
    return _start(account, title, kind, xc_id, ext, window, ip, user_agent)


def _start(  # noqa: PLR0913
    account: XtreamAccount,
    title: TitleRef | None,
    kind: TitleKind,
    xc_id: int,
    extension: str,
    window: TimeshiftWindow | None,
    ip: str | None,
    user_agent: str,
) -> PlayOutcome:
    if title is None:
        outcome: PlayOutcome = PlayRefused("NOT_FOUND")
    else:
        request = PlayRequest(
            user=account.user,
            device=account.device,
            title=title,
            extension=extension,
            client_ip=ip,
            user_agent=user_agent,
            window=window,
        )
        outcome = playback_starter().start(request)
    if isinstance(outcome, PlayRefused):
        logger.info(
            "xtream play refused",
            reason=outcome.code,
            user_id=account.user_id,
            device_id=str(account.device.pk),
            kind=kind.value,
            xc_id=xc_id,
        )
    return outcome
