"""Admin API routes of the media app, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.media import api

urlpatterns = [
    path("transcode-jobs", api.JobListView.as_view(), name="admin-transcode-jobs-list"),
    path("transcode-jobs/stream", api.JobStreamView.as_view(), name="admin-transcode-jobs-stream"),
    path(
        "transcode-jobs/<uuid:pk>/retry",
        api.JobRetryView.as_view(),
        name="admin-transcode-jobs-retry",
    ),
    path(
        "transcode-jobs/<uuid:pk>/cancel",
        api.JobCancelView.as_view(),
        name="admin-transcode-jobs-cancel",
    ),
    path(
        "transcode-jobs/<uuid:pk>/priority",
        api.JobPriorityView.as_view(),
        name="admin-transcode-jobs-priority",
    ),
]
