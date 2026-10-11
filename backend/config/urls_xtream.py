"""tv.<domain>: the Xtream-compatible API for IPTV apps (SPEC §7.5, built in M9).

Play URLs carry the credentials in the path; RequestLogMiddleware and the
redaction processor mask `/movie|series|live|timeshift/<u>/<p>/` in every log line.
"""

from django.urls import path, re_path

from apps.core import views as core_views
from apps.xtream_api import views

_CREDENTIALS = r"(?P<username>[^/]+)/(?P<password>[^/]+)"
_TITLE = r"(?P<xc_id>[1-9][0-9]{0,15})\.(?P<ext>[A-Za-z0-9]{1,8})"
_CHANNEL = r"(?P<xc_id>[1-9][0-9]{0,15})\.(?P<ext>ts|m3u8)"
_TIMESHIFT = (
    r"(?P<minutes>[1-9][0-9]{0,4})/"
    r"(?P<start>[0-9]{4}-[0-9]{2}-[0-9]{2}:[0-9]{2}-[0-9]{2}(?:-[0-9]{2})?)"
)

urlpatterns = [
    path("health", core_views.public_health, name="tv-health"),
    path("player_api.php", views.player_api, name="xtream-player-api"),
    path("get.php", views.get_playlist, name="xtream-playlist"),
    path("xmltv.php", views.guide, name="xtream-guide"),
    re_path(rf"^movie/{_CREDENTIALS}/{_TITLE}$", views.play_movie, name="xtream-play-movie"),
    re_path(rf"^series/{_CREDENTIALS}/{_TITLE}$", views.play_episode, name="xtream-play-episode"),
    # Live TV and catch-up (M12, ADR-0017).
    re_path(rf"^live/{_CREDENTIALS}/{_CHANNEL}$", views.play_live, name="xtream-live"),
    re_path(
        rf"^timeshift/{_CREDENTIALS}/{_TIMESHIFT}/{_CHANNEL}$",
        views.play_timeshift,
        name="xtream-timeshift",
    ),
    # The classic extension-less live URL; last, so it never shadows the paths above.
    re_path(
        rf"^{_CREDENTIALS}/(?P<xc_id>[1-9][0-9]{{0,15}})(?:\.(?P<ext>ts|m3u8))?$",
        views.play_live_short,
        name="xtream-live-short",
    ),
]

handler400 = views.bad_request
handler403 = views.permission_denied
handler404 = views.not_found
handler500 = views.server_error
