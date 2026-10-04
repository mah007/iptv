"""Customer API routes of the accounts app (ADR-0013), mounted under /api/v1/ by
config.urls_portal (session sign-in) and config.urls_api (token sign-in)."""

from django.urls import path

from apps.accounts import api_auth, api_customer

#: app.<domain>/api/v1/auth/: the portal's session sign-in.
portal_auth = [
    path("csrf", api_auth.CsrfView.as_view(), name="customer-auth-csrf"),
    path("login", api_customer.PortalLoginView.as_view(), name="customer-auth-login"),
    path("logout", api_customer.PortalLogoutView.as_view(), name="customer-auth-logout"),
    path(
        "password/forgot",
        api_customer.ForgotPasswordView.as_view(),
        name="customer-auth-password-forgot",
    ),
    path(
        "password/reset",
        api_customer.ResetPasswordView.as_view(),
        name="customer-auth-password-reset",
    ),
]

#: api.<domain>/api/v1/auth/: bearer tokens for apps.
token_auth = [
    path("login", api_customer.AppLoginView.as_view(), name="token-auth-login"),
    path("refresh", api_customer.AppRefreshView.as_view(), name="token-auth-refresh"),
    path("logout", api_customer.AppLogoutView.as_view(), name="token-auth-logout"),
    path(
        "password/forgot",
        api_customer.ForgotPasswordView.as_view(),
        name="token-auth-password-forgot",
    ),
    path(
        "password/reset",
        api_customer.ResetPasswordView.as_view(),
        name="token-auth-password-reset",
    ),
]

#: Both hosts: the signed-in customer and their devices.
urlpatterns = [
    path("me", api_customer.MeView.as_view(), name="customer-me"),
    path("me/devices", api_customer.MyDeviceListView.as_view(), name="customer-devices"),
    path(
        "me/devices/<uuid:pk>",
        api_customer.MyDeviceDetailView.as_view(),
        name="customer-device",
    ),
    path(
        "me/devices/<uuid:pk>/xtream-credentials",
        api_customer.MyDeviceCredentialsView.as_view(),
        name="customer-device-credentials",
    ),
]
