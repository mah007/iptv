"""Admin API routes of live TV, mounted under /api/v1/admin/ by config.urls_admin (ADR-0017)."""

from django.urls import path

from apps.live import api

urlpatterns = [
    path("live/overview", api.LiveOverviewView.as_view(), name="admin-live-overview"),
    path("live/channels", api.LiveChannelListView.as_view(), name="admin-live-channels"),
    path("live/channels/bulk", api.LiveChannelBulkView.as_view(), name="admin-live-channels-bulk"),
    path(
        "live/channels/reorder",
        api.LiveChannelReorderView.as_view(),
        name="admin-live-channels-reorder",
    ),
    path("live/channels/<uuid:pk>", api.LiveChannelDetailView.as_view(), name="admin-live-channel"),
    path(
        "live/channels/<uuid:pk>/logo",
        api.LiveChannelLogoView.as_view(),
        name="admin-live-channel-logo",
    ),
    path(
        "live/channels/<uuid:pk>/test",
        api.LiveChannelTestView.as_view(),
        name="admin-live-channel-test",
    ),
    path(
        "live/channels/<uuid:pk>/programmes",
        api.LiveChannelProgrammesView.as_view(),
        name="admin-live-channel-programmes",
    ),
    path("live/source-tests", api.SourceTestView.as_view(), name="admin-live-source-test"),
    path(
        "live/source-tests/<str:request_id>",
        api.SourceTestResultView.as_view(),
        name="admin-live-source-test-result",
    ),
    path("live/epg/sources", api.EpgSourceListView.as_view(), name="admin-live-epg-sources"),
    path(
        "live/epg/sources/<uuid:pk>",
        api.EpgSourceDetailView.as_view(),
        name="admin-live-epg-source",
    ),
    path(
        "live/epg/sources/<uuid:pk>/refresh",
        api.EpgSourceRefreshView.as_view(),
        name="admin-live-epg-source-refresh",
    ),
    path("live/epg/channels", api.EpgChannelSearchView.as_view(), name="admin-live-epg-channels"),
    path("live/epg/unmatched", api.EpgUnmatchedView.as_view(), name="admin-live-epg-unmatched"),
    path(
        "live/integrations",
        api.LiveIntegrationListView.as_view(),
        name="admin-live-integrations",
    ),
    path(
        "live/integrations/<uuid:pk>",
        api.LiveIntegrationDetailView.as_view(),
        name="admin-live-integration",
    ),
    path(
        "live/integrations/<uuid:pk>/sync",
        api.LiveIntegrationSyncView.as_view(),
        name="admin-live-integration-sync",
    ),
]
