"""Admin API routes of the edges app, mounted under /api/v1/admin/ (ADR-0022)."""

from django.urls import path

from apps.edges import api

urlpatterns = [
    path("edges", api.EdgeListView.as_view(), name="admin-edges"),
    path("edges/<uuid:pk>", api.EdgeDetailView.as_view(), name="admin-edge"),
    path("edges/<uuid:pk>/secret", api.EdgeSecretView.as_view(), name="admin-edge-secret"),
    path("edges/<uuid:pk>/test", api.EdgeTestView.as_view(), name="admin-edge-test"),
]
