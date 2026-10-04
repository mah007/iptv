"""Customer API routes of playback and engagement (ADR-0013), mounted under /api/v1/ by
config.urls_portal and config.urls_api."""

from django.urls import path

from apps.engagement import api

urlpatterns = [
    path("home", api.HomeView.as_view(), name="customer-home"),
    path("playback/start", api.PlaybackStartView.as_view(), name="customer-playback-start"),
    path(
        "playback/<uuid:pk>/progress",
        api.PlaybackProgressView.as_view(),
        name="customer-playback-progress",
    ),
    path("playback/<uuid:pk>/stop", api.PlaybackStopView.as_view(), name="customer-playback-stop"),
    path(
        "continue-watching",
        api.ContinueWatchingView.as_view(),
        name="customer-continue-watching",
    ),
    path("watch-history", api.WatchHistoryView.as_view(), name="customer-watch-history"),
    path(
        "watch-history/<uuid:pk>",
        api.WatchHistoryDetailView.as_view(),
        name="customer-watch-history-entry",
    ),
    path("favorites", api.FavoriteListView.as_view(), name="customer-favorites"),
    path(
        "favorites/<str:title_type>/<uuid:pk>",
        api.FavoriteDetailView.as_view(),
        name="customer-favorite",
    ),
    path("ratings", api.RatingView.as_view(), name="customer-ratings"),
    path("recommendations", api.RecommendationsView.as_view(), name="customer-recommendations"),
]
