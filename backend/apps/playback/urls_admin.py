"""Admin API routes of the playback app, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.playback import api

urlpatterns = [
    path("sessions", api.SessionListView.as_view(), name="admin-sessions-list"),
    path("sessions/stream", api.SessionStreamView.as_view(), name="admin-sessions-stream"),
    path("sessions/<uuid:pk>/kill", api.SessionKillView.as_view(), name="admin-sessions-kill"),
]
