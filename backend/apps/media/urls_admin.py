"""Admin API routes of the media app, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.media import api, api_titles

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
    # A title's media (ADR-0014): {pk} is a movie, series or episode id.
    path(
        "titles/<uuid:pk>/renditions",
        api_titles.TitleRenditionsView.as_view(),
        name="admin-title-renditions",
    ),
    path(
        "titles/<uuid:pk>/renditions/<uuid:rendition>",
        api_titles.TitleRenditionDetailView.as_view(),
        name="admin-title-rendition",
    ),
    path(
        "titles/<uuid:pk>/reprocess",
        api_titles.TitleReprocessView.as_view(),
        name="admin-title-reprocess",
    ),
    path(
        "titles/<uuid:pk>/tracks", api_titles.TitleTracksView.as_view(), name="admin-title-tracks"
    ),
    path(
        "titles/<uuid:pk>/tracks/<uuid:track>",
        api_titles.TitleTrackDetailView.as_view(),
        name="admin-title-track",
    ),
    path(
        "titles/<uuid:pk>/images", api_titles.TitleImagesView.as_view(), name="admin-title-images"
    ),
    path(
        "titles/<uuid:pk>/images/<uuid:image>",
        api_titles.TitleImageDetailView.as_view(),
        name="admin-title-image",
    ),
    path(
        "titles/<uuid:pk>/images/<uuid:image>/primary",
        api_titles.TitleImagePrimaryView.as_view(),
        name="admin-title-image-primary",
    ),
    path(
        "titles/<uuid:pk>/rematch",
        api_titles.TitleRematchView.as_view(),
        name="admin-title-rematch",
    ),
    path(
        "renditions/cleanup",
        api_titles.RenditionCleanupView.as_view(),
        name="admin-renditions-cleanup",
    ),
]
