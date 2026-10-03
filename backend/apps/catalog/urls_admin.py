"""Admin API routes of the catalog app, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.catalog import views

urlpatterns = [
    path("categories", views.CategoryListView.as_view(), name="admin-categories-list"),
]
