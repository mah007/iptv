"""Internal-only endpoints, reachable inside the Docker network but never via Traefik.

The edge's stream-auth (M7) and Prometheus /metrics (M2) live here too.
"""

from django.urls import path

from apps.core import views

urlpatterns = [
    path("internal/health/live", views.live, name="health-live"),
    path("internal/health/ready", views.ready, name="health-ready"),
]
