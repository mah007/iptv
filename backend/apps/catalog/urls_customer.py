"""Customer API routes of the catalogue (ADR-0013), mounted under /api/v1/ by
config.urls_portal and config.urls_api."""

from django.urls import path

from apps.catalog import api_customer

urlpatterns = [
    path("movies", api_customer.MovieListView.as_view(), name="customer-movies"),
    path("movies/<uuid:pk>", api_customer.MovieDetailView.as_view(), name="customer-movie"),
    path("series", api_customer.SeriesListView.as_view(), name="customer-series-list"),
    path("series/<uuid:pk>", api_customer.SeriesDetailView.as_view(), name="customer-series"),
    path(
        "series/<uuid:pk>/seasons/<int:number>",
        api_customer.SeasonDetailView.as_view(),
        name="customer-season",
    ),
    path("episodes/<uuid:pk>", api_customer.EpisodeDetailView.as_view(), name="customer-episode"),
    path("categories", api_customer.CategoryListView.as_view(), name="customer-categories"),
    path("genres", api_customer.GenreListView.as_view(), name="customer-genres"),
    path(
        "collections/<slug:slug>",
        api_customer.CollectionDetailView.as_view(),
        name="customer-collection",
    ),
    path("people/<uuid:pk>", api_customer.PersonDetailView.as_view(), name="customer-person"),
]
