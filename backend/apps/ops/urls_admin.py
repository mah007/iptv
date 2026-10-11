"""Admin API routes of the operations app, mounted under /api/v1/admin/ (ADR-0020)."""

from django.urls import path

from apps.ops import api

urlpatterns = [
    path("backups", api.BackupSummaryView.as_view(), name="admin-backups"),
    path("backups/runs", api.BackupRunListView.as_view(), name="admin-backups-runs"),
    path("backups/run", api.BackupRunNowView.as_view(), name="admin-backups-run"),
]
