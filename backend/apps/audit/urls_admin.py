"""Admin API routes of the audit app, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.audit import views

urlpatterns = [
    path("audit", views.AuditLogListView.as_view(), name="admin-audit-list"),
]
