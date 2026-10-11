"""Internal-only endpoints, reachable inside the Docker network but never via Traefik.

Health checks, Prometheus metrics, the media edge's stream-auth (ADR-0007) and the
monitoring host's forward-auth and Alertmanager's webhook (ADR-0018).
"""

from django.urls import path

from apps.accounts.monitoring_views import forward_auth
from apps.core import views
from apps.notifications.views_internal import alert_webhook
from apps.playback.views import stream_auth

urlpatterns = [
    path("internal/health/live", views.live, name="health-live"),
    path("internal/health/ready", views.ready, name="health-ready"),
    path("internal/stream-auth", stream_auth, name="stream-auth"),
    # Traefik's forward-auth for grafana.<domain> (ADR-0018).
    path("internal/monitoring-auth", forward_auth, name="monitoring-auth"),
    # Alertmanager's webhook: alerts go out by email and Telegram from the settings.
    path("internal/alerts", alert_webhook, name="alert-webhook"),
    path("metrics", views.metrics, name="metrics"),
]
