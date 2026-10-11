"""Admin API routes of the dashboard, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.dashboard import api, integrations_api, monitoring_api

urlpatterns = [
    path("dashboard/kpis", api.KpisView.as_view(), name="admin-dashboard-kpis"),
    path("dashboard/timeseries", api.TimeseriesView.as_view(), name="admin-dashboard-timeseries"),
    path("dashboard/activity", api.ActivityView.as_view(), name="admin-dashboard-activity"),
    path("health", api.HealthView.as_view(), name="admin-system-health"),
    path("storage", api.StorageView.as_view(), name="admin-storage-usage"),
    # O1 (ADR-0018): the monitoring overlay's status and a test alert.
    path("monitoring", monitoring_api.MonitoringStatusView.as_view(), name="admin-monitoring"),
    path(
        "monitoring/test-alert",
        monitoring_api.MonitoringTestAlertView.as_view(),
        name="admin-monitoring-test-alert",
    ),
    # CFG (ADR-0019): the Integrations and System pages.
    path(
        "integrations",
        integrations_api.IntegrationListView.as_view(),
        name="admin-integrations",
    ),
    path(
        "integrations/<slug:name>/test",
        integrations_api.IntegrationTestView.as_view(),
        name="admin-integrations-test",
    ),
    path("system", integrations_api.SystemInfoView.as_view(), name="admin-system-info"),
    # SPEC §10 admin/users/{id}/history (customers are users; the admin API calls them customers).
    path(
        "customers/<uuid:pk>/history",
        api.CustomerHistoryView.as_view(),
        name="admin-customers-history",
    ),
]
