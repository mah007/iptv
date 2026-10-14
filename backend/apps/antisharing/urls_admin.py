"""Admin API routes of anti-sharing and IP geolocation, under /api/v1/admin/ (ADR-0021)."""

from django.urls import path

from apps.antisharing import api

urlpatterns = [
    path("customers/<uuid:pk>/risk", api.CustomerRiskView.as_view(), name="admin-customers-risk"),
    path(
        "customers/<uuid:pk>/risk/review",
        api.CustomerRiskReviewView.as_view(),
        name="admin-customers-risk-review",
    ),
    path(
        "customers/<uuid:pk>/risk/warn",
        api.CustomerRiskWarnView.as_view(),
        name="admin-customers-risk-warn",
    ),
    path(
        "customers/<uuid:pk>/risk/reset-credentials",
        api.CustomerRiskResetView.as_view(),
        name="admin-customers-risk-reset",
    ),
    path(
        "customers/<uuid:pk>/risk/suspend",
        api.CustomerRiskSuspendView.as_view(),
        name="admin-customers-risk-suspend",
    ),
    path("geoip", api.GeoIpView.as_view(), name="admin-geoip"),
    path("geoip/update", api.GeoIpUpdateView.as_view(), name="admin-geoip-update"),
    path("geoip/lookup", api.GeoIpLookupView.as_view(), name="admin-geoip-lookup"),
]
