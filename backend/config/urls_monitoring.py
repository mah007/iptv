"""grafana.<domain>: the monitoring host (ADR-0018).

Traefik sends Grafana, /prometheus and /alertmanager to their containers behind the
forward-auth check; only the sign-in exchange (/_sso/...) reaches Django.
"""

from django.urls import path

from apps.accounts import monitoring_views

urlpatterns = [
    path("_sso/callback", monitoring_views.sso_callback, name="monitoring-sso-callback"),
    path("_sso/logout", monitoring_views.sso_logout, name="monitoring-sso-logout"),
]
