"""Internal-only endpoints, reachable inside the Docker network but never via Traefik.

Health checks, Prometheus metrics, the media edge's stream-auth (ADR-0007) and the
monitoring host's forward-auth and Alertmanager's webhook (ADR-0018).
"""

from django.urls import path

from apps.accounts.monitoring_views import forward_auth
from apps.core import views
from apps.notifications.views_internal import alert_fallback, alert_webhook
from apps.ops.views_internal import agent_job, agent_poll
from apps.playback.views import stream_auth

urlpatterns = [
    path("internal/health/live", views.live, name="health-live"),
    path("internal/health/ready", views.ready, name="health-ready"),
    path("internal/stream-auth", stream_auth, name="stream-auth"),
    # Traefik's forward-auth for grafana.<domain> (ADR-0018).
    path("internal/monitoring-auth", forward_auth, name="monitoring-auth"),
    # Alertmanager's webhook: alerts go out by email and Telegram from the settings.
    path("internal/alerts", alert_webhook, name="alert-webhook"),
    # Its own fallback channels, from the admin's settings (CFG, ADR-0019).
    path("internal/alert-fallback", alert_fallback, name="alert-fallback"),
    # The backup service's configuration, jobs and results (ADR-0020).
    path("internal/backups/agent/poll", agent_poll, name="backup-agent-poll"),
    path("internal/backups/agent/jobs/<uuid:run_id>", agent_job, name="backup-agent-job"),
    path("metrics", views.metrics, name="metrics"),
]
