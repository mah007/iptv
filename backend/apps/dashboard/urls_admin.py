"""Admin API routes of the dashboard, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.dashboard import api

urlpatterns = [
    path("dashboard/kpis", api.KpisView.as_view(), name="admin-dashboard-kpis"),
]
