"""tv.<domain>: the Xtream-compatible API for IPTV apps (SPEC §7.5, built in M9)."""

from django.urls import path

from apps.core import views

urlpatterns = [
    path("health", views.public_health, name="tv-health"),
]
