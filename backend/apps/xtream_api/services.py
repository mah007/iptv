"""Xtream actions: catalog JSON, the login payload, the playlist and play starts (SPEC §7.5).

Views parse HTTP and authenticate; these functions decide what an account gets.
Catalog responses come from the response cache (cache.py) or are built from the
catalog source (source.py) with the pure builders (payloads.py, playlist.py).
Accounts that are not "Active" get valid, empty catalog responses.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

import orjson
import structlog
from django.utils import timezone

from apps.core.services import get_setting
from apps.xtream_api import cache, payloads, playlist
from apps.xtream_api.auth import XtreamAccount, server_info
from apps.xtream_api.dto import CategoryItem, CategoryKind, EpisodeRef, TitleKind
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


def _allowed(account: XtreamAccount, kind: Literal["vod", "series"]) -> bool:
    scope = account.scope
    return account.active and (scope.allow_movies if kind == "vod" else scope.allow_series)


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


def _constant(body: bytes) -> Handler:
    def handle(account: XtreamAccount, params: Params) -> JsonResult:
        return JsonResult(body)

    return handle


_HANDLERS: dict[str, Handler] = {
    "get_vod_categories": _category_list("vod"),
    "get_series_categories": _category_list("series"),
    "get_vod_streams": _stream_list("vod"),
    "get_vod_info": _vod_info,
    "get_series": _stream_list("series"),
    "get_series_info": _series_info,
    # Live TV and its guide arrive in M12: empty, correctly typed.
    "get_live_categories": _constant(payloads.EMPTY_LIST),
    "get_live_streams": _constant(payloads.EMPTY_LIST),
    "get_short_epg": _constant(payloads.EMPTY_EPG),
    "get_simple_data_table": _constant(payloads.EMPTY_EPG),
}


# --- get.php and xmltv.php ------------------------------------------------------------------


def playlist_body(account: XtreamAccount, *, username: str, password: str, output: str) -> bytes:
    """The m3u_plus playlist (compat/m3u.md): what player_api.php shows the account."""
    entries: list[playlist.Entry] = []
    if account.active and get_setting("features.include_vod_in_m3u"):
        body = cache.cached(account.scope, account.locale, "m3u", lambda: _playlist_rows(account))
        rows = cast("list[list[object]]", orjson.loads(body or payloads.EMPTY_LIST))
        entries = [playlist.Entry.from_row(row) for row in rows]
    return playlist.render(
        entries,
        origin=server_info().origin,
        username=username,
        password=password,
        live_ext=playlist.live_extension(output),
    )


def _playlist_rows(account: XtreamAccount) -> list[list[object]]:
    source, scope, locale = catalog_source(), account.scope, account.locale
    movies: list[payloads.Payload] = []
    movie_categories: list[payloads.Payload] = []
    if scope.allow_movies:
        categories = _source_categories(account, "vod")
        movies = payloads.vod_streams(source.movies(scope), categories, locale)
        movie_categories = payloads.categories(categories, locale)
    series: list[payloads.Payload] = []
    series_categories: list[payloads.Payload] = []
    episodes: tuple[EpisodeRef, ...] = ()
    if scope.allow_series:
        categories = _source_categories(account, "series")
        series = payloads.series_list(source.series_list(scope), categories, locale)
        series_categories = payloads.categories(categories, locale)
        episodes = tuple(source.episodes(scope))
    entries = playlist.entries(movies, movie_categories, series, series_categories, episodes)
    return [entry.row() for entry in entries]


def guide_body(account: XtreamAccount) -> bytes:
    """xmltv.php: the account's channels and programmes; empty until live TV (M12)."""
    return playlist.empty_guide(str(get_setting("branding.service_name_en")))


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
    if title is None:
        outcome: PlayOutcome = PlayRefused("NOT_FOUND")
    else:
        request = PlayRequest(
            user=account.user,
            device=account.device,
            title=title,
            extension=extension.lower(),
            client_ip=ip,
            user_agent=user_agent,
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
