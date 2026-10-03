import { isApiError, useAuthLogin, useAuthMfaVerify } from "@smart-iptv/api";
import { useQueryClient } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { LoginForm } from "../features/auth/login-form";
import { MfaForm } from "../features/auth/mfa-form";
import { meQueryOptions, safeRedirect } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";

const loginApi = getRouteApi("/auth/login");
const mfaApi = getRouteApi("/auth/login/mfa");

/** problem+json code of a failed sign-in step; network failures read as unavailable. */
function failureCode(error: unknown): string {
  return isApiError(error) ? error.code : "UNAVAILABLE";
}

/** Step 1: username or email and password (`POST /api/v1/auth/login`). */
export function LoginPage() {
  const { t } = useTranslation();
  usePageTitle(t("auth.login.title"));
  const navigate = useNavigate();
  const search = loginApi.useSearch();
  const { signIn } = loginApi.useRouteContext();
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const login = useAuthLogin();

  return (
    <LoginForm
      errorCode={errorCode}
      onSubmit={async (values) => {
        setErrorCode(null);
        try {
          const result = await login.mutateAsync({ data: values });
          signIn.setEnrolment(
            result.status === "mfa_setup_required" ? (result.otpauth_uri ?? null) : null,
          );
          await navigate({ to: "/login/mfa", search });
        } catch (error) {
          setErrorCode(failureCode(error));
        }
      }}
    />
  );
}

/** Step 2: the authenticator code, enrolling the app first on a first sign-in. */
export function MfaPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const search = mfaApi.useSearch();
  const { signIn } = mfaApi.useRouteContext();
  // Read once: the enrolment belongs to the sign-in that brought us here.
  const [otpauthUri] = useState(() => signIn.enrolment());
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const verify = useAuthMfaVerify();
  usePageTitle(otpauthUri ? t("auth.mfa.setupTitle") : t("auth.mfa.verifyTitle"));

  async function onSubmit({ code }: { code: string }): Promise<void> {
    setErrorCode(null);
    try {
      const me = await verify.mutateAsync({ data: { code } });
      signIn.setEnrolment(null);
      queryClient.setQueryData(meQueryOptions().queryKey, me);
      await navigate({ href: safeRedirect(search.redirect), replace: true });
    } catch (error) {
      setErrorCode(failureCode(error));
    }
  }

  function onBack(): void {
    signIn.setEnrolment(null);
    void navigate({ to: "/login", search });
  }

  return otpauthUri ? (
    <MfaForm
      mode="setup"
      otpauthUri={otpauthUri}
      errorCode={errorCode}
      onSubmit={onSubmit}
      onBack={onBack}
    />
  ) : (
    <MfaForm mode="verify" errorCode={errorCode} onSubmit={onSubmit} onBack={onBack} />
  );
}
