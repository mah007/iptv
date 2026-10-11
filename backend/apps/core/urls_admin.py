"""Admin API routes of the core app, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.core import api

urlpatterns = [
    path("settings", api.SettingListView.as_view(), name="admin-settings-list"),
    path("settings/<str:key>", api.SettingDetailView.as_view(), name="admin-settings-detail"),
    # CFG (ADR-0019): move a server-config value in; upload a logo.
    path("settings/<str:key>/adopt", api.SettingAdoptView.as_view(), name="admin-settings-adopt"),
    path("settings/<str:key>/image", api.SettingImageView.as_view(), name="admin-settings-image"),
]
