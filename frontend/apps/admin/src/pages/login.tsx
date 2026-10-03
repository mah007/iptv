import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { AuthErrorCode } from "../features/auth/errors";
import { LoginForm } from "../features/auth/login-form";
import { MfaForm } from "../features/auth/mfa-form";
import { usePageTitle } from "../lib/page-title";

/*
 * Sign-in screens. The forms are complete (validation, error display, busy
 * state); the sign-in API (`/api/v1/auth/*`) is connected through the
 * generated client in the admin-pages slice. Until then a submission reports
 * the service as unavailable rather than pretending to sign anyone in.
 */
const NOT_CONNECTED: AuthErrorCode = "UNAVAILABLE";

export function LoginPage() {
  const { t } = useTranslation();
  usePageTitle(t("auth.login.title"));
  const [errorCode, setErrorCode] = useState<AuthErrorCode | null>(null);
  return (
    <LoginForm
      errorCode={errorCode}
      onSubmit={() => {
        setErrorCode(NOT_CONNECTED);
      }}
    />
  );
}

export function MfaPage() {
  const { t } = useTranslation();
  usePageTitle(t("auth.mfa.verifyTitle"));
  const navigate = useNavigate();
  const [errorCode, setErrorCode] = useState<AuthErrorCode | null>(null);
  return (
    <MfaForm
      mode="verify"
      errorCode={errorCode}
      onSubmit={() => {
        setErrorCode(NOT_CONNECTED);
      }}
      onBack={() => {
        void navigate({ to: "/login" });
      }}
    />
  );
}
