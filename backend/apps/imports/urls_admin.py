"""Admin API routes of imports, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.imports import api

urlpatterns = [
    path("imports/status", api.ImportStatusView.as_view(), name="admin-imports-status"),
    path("imports/sources", api.ImportSourceListView.as_view(), name="admin-import-sources"),
    path(
        "imports/sources/<uuid:pk>",
        api.ImportSourceDetailView.as_view(),
        name="admin-import-source",
    ),
    path(
        "imports/sources/<uuid:pk>/test",
        api.ImportSourceTestView.as_view(),
        name="admin-import-source-test",
    ),
    path(
        "imports/sources/<uuid:pk>/browse",
        api.ImportSourceBrowseView.as_view(),
        name="admin-import-source-browse",
    ),
    path(
        "imports/requests/<str:request_id>",
        api.ImportRequestView.as_view(),
        name="admin-import-request",
    ),
    path("imports/jobs", api.ImportJobListView.as_view(), name="admin-import-jobs"),
    path("imports/jobs/<uuid:pk>", api.ImportJobDetailView.as_view(), name="admin-import-job"),
    path(
        "imports/jobs/<uuid:pk>/cancel",
        api.ImportJobCancelView.as_view(),
        name="admin-import-job-cancel",
    ),
    path("imports/links", api.ImportLinkView.as_view(), name="admin-import-links"),
]
