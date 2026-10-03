"""Admin API routes of the core app, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.core import api

urlpatterns = [
    path("settings", api.SettingListView.as_view(), name="admin-settings-list"),
    path("settings/<str:key>", api.SettingDetailView.as_view(), name="admin-settings-detail"),
]
