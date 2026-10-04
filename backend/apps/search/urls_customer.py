"""Customer API route of search (ADR-0013), mounted under /api/v1/ by config.urls_portal
and config.urls_api."""

from django.urls import path

from apps.search import api

urlpatterns = [path("search", api.SearchView.as_view(), name="customer-search")]
