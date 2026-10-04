"""Admin API routes of the dashboard, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.dashboard import api

urlpatterns = [
    path("dashboard/kpis", api.KpisView.as_view(), name="admin-dashboard-kpis"),
    path("dashboard/timeseries", api.TimeseriesView.as_view(), name="admin-dashboard-timeseries"),
    path("dashboard/activity", api.ActivityView.as_view(), name="admin-dashboard-activity"),
    path("health", api.HealthView.as_view(), name="admin-system-health"),
    path("storage", api.StorageView.as_view(), name="admin-storage-usage"),
    # SPEC §10 admin/users/{id}/history (customers are users; the admin API calls them customers).
    path(
        "customers/<uuid:pk>/history",
        api.CustomerHistoryView.as_view(),
        name="admin-customers-history",
    ),
]
