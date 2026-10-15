"""Customer API routes of live TV (Q1, ADR-0023), mounted under /api/v1/ by
config.urls_portal and config.urls_api."""

from django.urls import path

from apps.live import api_customer

urlpatterns = [
    path("live/channels", api_customer.LiveChannelListView.as_view(), name="customer-live"),
    path(
        "live/channels/<uuid:pk>/guide",
        api_customer.LiveChannelGuideView.as_view(),
        name="customer-live-guide",
    ),
]
