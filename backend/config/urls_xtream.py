"""tv.<domain>: the Xtream-compatible API for IPTV apps (SPEC §7.5, built in M9).

Play URLs carry the credentials in the path; RequestLogMiddleware and the
redaction processor mask `/movie|series|live|timeshift/<u>/<p>/` in every log line.
"""

from django.urls import path, re_path

from apps.core import views as core_views
from apps.xtream_api import views

_CREDENTIALS = r"(?P<username>[^/]+)/(?P<password>[^/]+)"
_TITLE = r"(?P<xc_id>[1-9][0-9]{0,15})\.(?P<ext>[A-Za-z0-9]{1,8})"

urlpatterns = [
    path("health", core_views.public_health, name="tv-health"),
    path("player_api.php", views.player_api, name="xtream-player-api"),
    path("get.php", views.get_playlist, name="xtream-playlist"),
    path("xmltv.php", views.guide, name="xtream-guide"),
    re_path(rf"^movie/{_CREDENTIALS}/{_TITLE}$", views.play_movie, name="xtream-play-movie"),
    re_path(rf"^series/{_CREDENTIALS}/{_TITLE}$", views.play_episode, name="xtream-play-episode"),
]

handler400 = views.bad_request
handler403 = views.permission_denied
handler404 = views.not_found
handler500 = views.server_error
