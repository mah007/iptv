"""Admin sign-in routes, mounted under /api/v1/auth/ by config.urls_admin."""

from django.urls import path

from apps.accounts import api_auth

urlpatterns = [
    path("csrf", api_auth.CsrfView.as_view(), name="auth-csrf"),
    path("login", api_auth.LoginView.as_view(), name="auth-login"),
    path("mfa/verify", api_auth.MfaVerifyView.as_view(), name="auth-mfa-verify"),
    path("logout", api_auth.LogoutView.as_view(), name="auth-logout"),
    path("me", api_auth.MeView.as_view(), name="auth-me"),
]
