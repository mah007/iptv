"""Admin sign-in routes, mounted under /api/v1/auth/ by config.urls_admin."""

from django.urls import path

from apps.accounts import api_auth

urlpatterns = [
    path("csrf", api_auth.CsrfView.as_view(), name="auth-csrf"),
    path("login", api_auth.LoginView.as_view(), name="auth-login"),
    path("mfa/verify", api_auth.MfaVerifyView.as_view(), name="auth-mfa-verify"),
    path(
        "mfa/webauthn/options",
        api_auth.MfaWebAuthnOptionsView.as_view(),
        name="auth-mfa-webauthn-options",
    ),
    path(
        "mfa/webauthn/verify",
        api_auth.MfaWebAuthnVerifyView.as_view(),
        name="auth-mfa-webauthn-verify",
    ),
    path("step-up", api_auth.StepUpView.as_view(), name="auth-step-up"),
    path("password", api_auth.PasswordChangeView.as_view(), name="auth-password-change"),
    path("step-up/options", api_auth.StepUpOptionsView.as_view(), name="auth-step-up-options"),
    path(
        "webauthn/credentials",
        api_auth.WebAuthnCredentialListView.as_view(),
        name="auth-webauthn-credentials",
    ),
    path(
        "webauthn/credentials/options",
        api_auth.WebAuthnRegisterOptionsView.as_view(),
        name="auth-webauthn-credentials-options",
    ),
    path(
        "webauthn/credentials/<uuid:pk>",
        api_auth.WebAuthnCredentialDetailView.as_view(),
        name="auth-webauthn-credential",
    ),
    path("logout", api_auth.LogoutView.as_view(), name="auth-logout"),
    path("me", api_auth.MeView.as_view(), name="auth-me"),
]
