"""Admin API routes of the notifications app, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.notifications import api

urlpatterns = [
    path("notifications", api.OutboxListView.as_view(), name="admin-notifications-list"),
    path(
        "notifications/<uuid:pk>",
        api.OutboxDetailView.as_view(),
        name="admin-notifications-detail",
    ),
    path(
        "notifications/<uuid:pk>/retry",
        api.OutboxRetryView.as_view(),
        name="admin-notifications-retry",
    ),
    path("templates", api.TemplateListView.as_view(), name="admin-templates-list"),
    path("templates/preview", api.TemplatePreviewView.as_view(), name="admin-templates-preview"),
    path(
        "templates/<slug:key>/<slug:channel>/<slug:locale>",
        api.TemplateDetailView.as_view(),
        name="admin-templates-detail",
    ),
    path(
        "templates/<slug:key>/<slug:channel>/<slug:locale>/test",
        api.TemplateTestView.as_view(),
        name="admin-templates-test",
    ),
]
