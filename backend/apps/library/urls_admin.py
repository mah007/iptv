"""Admin API routes of the library app, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.library import sse, views

urlpatterns = [
    path("libraries", views.LibraryListView.as_view(), name="admin-libraries-list"),
    path("libraries/<uuid:pk>", views.LibraryDetailView.as_view(), name="admin-library"),
    path("libraries/<uuid:pk>/scan", views.LibraryScanView.as_view(), name="admin-library-scan"),
    path("libraries/<uuid:pk>/scan/stream", sse.scan_stream, name="admin-library-scan-stream"),
    path("scans", views.ScanListView.as_view(), name="admin-scans-list"),
    path("scans/<uuid:pk>", views.ScanDetailView.as_view(), name="admin-scan"),
]
