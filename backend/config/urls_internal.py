"""Internal-only endpoints, reachable inside the Docker network but never via Traefik.

Health checks, Prometheus metrics, and the media edge's stream-auth (ADR-0007).
"""

from django.urls import path

from apps.core import views
from apps.playback.views import stream_auth

urlpatterns = [
    path("internal/health/live", views.live, name="health-live"),
    path("internal/health/ready", views.ready, name="health-ready"),
    path("internal/stream-auth", stream_auth, name="stream-auth"),
    path("metrics", views.metrics, name="metrics"),
]
