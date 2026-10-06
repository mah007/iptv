"""tv.<domain>: player_api.php, get.php, xmltv.php and play URLs for IPTV apps (SPEC §7.5).

- Methods: GET and POST (credentials and parameters in the query or the form
  body; the body wins), plus HEAD. CSRF does not apply: there is no session.
- Failed authentication: player_api.php answers HTTP 200 with
  `{"user_info":{"auth":0}}`, get.php and xmltv.php an empty 403, play URLs an
  empty 404. Each is the same for an unknown user and a wrong password.
- Nothing here logs: RequestLogMiddleware logs every request with Xtream
  credentials masked in the path and never logs the query string.
"""

from datetime import UTC, datetime

from django.http import HttpRequest, HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.gzip import gzip_page
from django.views.decorators.http import require_http_methods

from apps.core.http import client_ip
from apps.xtream_api import auth, payloads, playback, playlist, services
from apps.xtream_api.dto import TimeshiftWindow, TitleKind
from apps.xtream_api.playback import PlayOutcome, PlayStarted

JSON_CONTENT_TYPE = "application/json"
USER_AGENT_MAX = 256  # PlaybackSession.user_agent


def _params(request: HttpRequest) -> dict[str, str]:
    params = request.GET.dict()
    if request.method == "POST":
        params.update(request.POST.dict())
    return params


def _json(body: bytes, status: int = 200) -> HttpResponse:
    return HttpResponse(body, status=status, content_type=JSON_CONTENT_TYPE)


def _empty(status: int) -> HttpResponse:
    return HttpResponse(b"", status=status, content_type="text/plain; charset=utf-8")


def _account(request: HttpRequest, params: dict[str, str]) -> auth.XtreamAccount | None:
    return auth.authenticate(
        params.get("username", ""), params.get("password", ""), ip=client_ip(request)
    )


@csrf_exempt
@never_cache
@gzip_page
@require_http_methods(["GET", "POST", "HEAD"])
def player_api(request: HttpRequest) -> HttpResponse:
    """The login payload (no action, an unknown one, get_account_info) or a catalog action."""
    params = _params(request)
    account = _account(request, params)
    if account is None:
        return _json(payloads.AUTH_FAILURE)
    result = services.catalog_action(account, params.get("action", "").strip(), params)
    if result is None:
        return _json(
            services.login_body(account, username=params["username"], password=params["password"])
        )
    return _json(result.body, result.status)


@csrf_exempt
@never_cache
@gzip_page
@require_http_methods(["GET", "POST", "HEAD"])
def get_playlist(request: HttpRequest) -> HttpResponse:
    """get.php?type=m3u_plus&output=ts|m3u8|mp4 (compat/m3u.md)."""
    params = _params(request)
    account = _account(request, params)
    if account is None:
        return _empty(403)
    body = services.playlist_body(
        account,
        username=params["username"],
        password=params["password"],
        output=params.get("output", ""),
    )
    response = HttpResponse(body, content_type=playlist.CONTENT_TYPE)
    response["Content-Disposition"] = 'attachment; filename="playlist.m3u"'
    return response


@csrf_exempt
@never_cache
@gzip_page
@require_http_methods(["GET", "POST", "HEAD"])
def guide(request: HttpRequest) -> HttpResponse:
    """xmltv.php (compat/xmltv.md)."""
    params = _params(request)
    account = _account(request, params)
    if account is None:
        return _empty(403)
    return HttpResponse(services.guide_body(account), content_type=playlist.GUIDE_CONTENT_TYPE)


@never_cache
@require_http_methods(["GET", "HEAD"])
def play_movie(
    request: HttpRequest, username: str, password: str, xc_id: str, ext: str
) -> HttpResponse:
    return _play(request, TitleKind.MOVIE, (username, password), int(xc_id), ext)


@never_cache
@require_http_methods(["GET", "HEAD"])
def play_episode(
    request: HttpRequest, username: str, password: str, xc_id: str, ext: str
) -> HttpResponse:
    return _play(request, TitleKind.EPISODE, (username, password), int(xc_id), ext)


@never_cache
@require_http_methods(["GET", "HEAD"])
def play_live(
    request: HttpRequest, username: str, password: str, xc_id: str, ext: str
) -> HttpResponse:
    """/live/{u}/{p}/{id}.{ts|m3u8} (M12)."""
    return _play_live(request, (username, password), int(xc_id), ext, None)


@never_cache
@require_http_methods(["GET", "HEAD"])
def play_live_short(
    request: HttpRequest, username: str, password: str, xc_id: str, ext: str | None = None
) -> HttpResponse:
    """/{u}/{p}/{id}: the classic live URL; without an extension, the channel's default."""
    return _play_live(request, (username, password), int(xc_id), ext, None)


#: Timeshift starts as apps send them: server time (UTC), minutes, optional seconds.
_TIMESHIFT_FORMATS = ("%Y-%m-%d:%H-%M", "%Y-%m-%d:%H-%M-%S")


def timeshift_window(minutes: str, start: str) -> TimeshiftWindow | None:
    for pattern in _TIMESHIFT_FORMATS:
        try:
            moment = datetime.strptime(start, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
        return TimeshiftWindow(start=moment, minutes=int(minutes))
    return None


@never_cache
@require_http_methods(["GET", "HEAD"])
def play_timeshift(  # noqa: PLR0913, PLR0917 (the URL's parts)
    request: HttpRequest,
    username: str,
    password: str,
    minutes: str,
    start: str,
    xc_id: str,
    ext: str,
) -> HttpResponse:
    """/timeshift/{u}/{p}/{minutes}/{YYYY-MM-DD:HH-MM}/{id}.{ts|m3u8}: catch-up (M12)."""
    window = timeshift_window(minutes, start)
    if window is None:
        return _empty(404)
    return _play_live(request, (username, password), int(xc_id), ext, window)


def _play(
    request: HttpRequest, kind: TitleKind, credentials: tuple[str, str], xc_id: int, ext: str
) -> HttpResponse:
    """302 to the signed edge URL, or an empty refusal with X-Reason."""
    ip = client_ip(request)
    account = auth.authenticate(*credentials, ip=ip)
    if account is None:
        return _empty(404)
    outcome = services.start_play(
        account,
        kind=kind,
        xc_id=xc_id,
        extension=ext,
        ip=ip,
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:USER_AGENT_MAX],
    )
    return _answer(outcome)


def _play_live(
    request: HttpRequest,
    credentials: tuple[str, str],
    xc_id: int,
    ext: str | None,
    window: TimeshiftWindow | None,
) -> HttpResponse:
    ip = client_ip(request)
    account = auth.authenticate(*credentials, ip=ip)
    if account is None:
        return _empty(404)
    outcome = services.start_live(
        account,
        xc_id=xc_id,
        extension=ext,
        window=window,
        ip=ip,
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:USER_AGENT_MAX],
    )
    return _answer(outcome)


def _answer(outcome: PlayOutcome) -> HttpResponse:
    if isinstance(outcome, PlayStarted):
        response = HttpResponse(status=302)
        response["Location"] = outcome.url
        return _cross_origin(response)
    refusal = playback.refusal(outcome)
    response = _empty(refusal.status)
    response["X-Reason"] = refusal.reason
    if refusal.retry_after_s is not None:
        response["Retry-After"] = str(refusal.retry_after_s)
    return _cross_origin(response)


def _cross_origin(response: HttpResponse) -> HttpResponse:
    """Web players (hls.js, mpegts.js in a PWA such as IPTVnator) fetch play URLs across
    origins and must see the redirect. No cookie or credential header is involved (the
    credentials are in the URL itself), so any origin may read it (ADR-0017)."""
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Expose-Headers"] = "X-Reason, Retry-After"
    return response


# --- Error handlers for this host (config/urls_xtream.py): empty bodies, like the rest.


def bad_request(request: HttpRequest, exception: Exception) -> HttpResponse:
    return _empty(400)


def permission_denied(request: HttpRequest, exception: Exception) -> HttpResponse:
    return _empty(403)


def not_found(request: HttpRequest, exception: Exception) -> HttpResponse:
    return _empty(404)


def server_error(request: HttpRequest) -> HttpResponse:
    return _empty(500)
