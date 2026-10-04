"""Admin API routes of the catalog app, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.catalog import views

urlpatterns = [
    path("categories", views.CategoryListView.as_view(), name="admin-categories-list"),
    path(
        "categories/reorder", views.CategoryReorderView.as_view(), name="admin-categories-reorder"
    ),
    path("categories/<uuid:pk>", views.CategoryDetailView.as_view(), name="admin-category"),
    path("movies", views.MovieListView.as_view(), name="admin-movies-list"),
    path("movies/<uuid:pk>", views.MovieDetailView.as_view(), name="admin-movie"),
    path(
        "movies/<uuid:pk>/refresh-metadata",
        views.MovieRefreshView.as_view(),
        name="admin-movie-refresh",
    ),
    path("series", views.SeriesListView.as_view(), name="admin-series-list"),
    path("series/<uuid:pk>", views.SeriesDetailView.as_view(), name="admin-series"),
    path(
        "series/<uuid:pk>/refresh-metadata",
        views.SeriesRefreshView.as_view(),
        name="admin-series-refresh",
    ),
    path("review-queue", views.ReviewListView.as_view(), name="admin-review-list"),
    path("review-queue/<uuid:pk>", views.ReviewDetailView.as_view(), name="admin-review"),
    path(
        "review-queue/<uuid:pk>/resolve",
        views.ReviewResolveView.as_view(),
        name="admin-review-resolve",
    ),
    path("review-queue/<uuid:pk>/skip", views.ReviewSkipView.as_view(), name="admin-review-skip"),
    path("metadata/search", views.MetadataSearchView.as_view(), name="admin-metadata-search"),
    # Collections (C1, ADR-0013): curated rows for the portal's home and their pages.
    path("collections", views.CollectionListView.as_view(), name="admin-collections-list"),
    path("collections/<uuid:pk>", views.CollectionDetailView.as_view(), name="admin-collection"),
]
