import { zodResolver } from "@hookform/resolvers/zod";
import {
  isApiError,
  useAuthLogin,
  useAuthPasswordForgot,
  useAuthPasswordReset,
} from "@smart-iptv/api-portal";
import {
  Alert,
  Button,
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  PasswordInput,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { MailCheck } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { authErrorKey, resetErrorKeys } from "../features/auth/errors";
import {
  forgotSchema,
  loginSchema,
  newPasswordSchema,
  PASSWORD_MIN_LENGTH,
  type ForgotValues,
  type LoginInput,
  type LoginValues,
  type NewPasswordValues,
} from "../features/auth/schemas";
import { meQueryOptions, safeRedirect } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";

const loginApi = getRouteApi("/auth/login");
const resetApi = getRouteApi("/auth/reset-password");

function AuthCard({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="grid gap-6">
      <div className="grid gap-1.5 text-center">
        <h1 className="text-2xl font-semibold text-foreground ltr:tracking-tight">{title}</h1>
        {subtitle ? <p className="text-ui text-muted-foreground">{subtitle}</p> : null}
      </div>
      <div className="grid gap-4 rounded-card border border-border bg-card p-6 shadow-elevation">
        {children}
      </div>
      {footer ? <div className="text-center text-ui text-muted-foreground">{footer}</div> : null}
    </div>
  );
}

/** Sign in with a username, an email or a phone number and a password. */
export function LoginPage() {
  const { t } = useTranslation();
  usePageTitle(t("auth.login.title"));
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const search = loginApi.useSearch();
  const [error, setError] = useState<unknown>(null);
  const login = useAuthLogin();
  const form = useForm<LoginInput, unknown, LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { login: "", password: "" },
  });
  const submitting = form.formState.isSubmitting;

  async function onSubmit(values: LoginValues): Promise<void> {
    setError(null);
    try {
      const me = await login.mutateAsync({ data: values });
      // A different customer may sign in on this browser: start from a clean cache.
      queryClient.clear();
      queryClient.setQueryData(meQueryOptions().queryKey, me);
      await navigate({ href: safeRedirect(search.redirect), replace: true });
    } catch (failure) {
      setError(failure);
    }
  }

  return (
    <AuthCard
      title={t("auth.login.title")}
      subtitle={t("auth.login.subtitle")}
      footer={t("auth.login.help")}
    >
      {error !== null ? (
        <Alert tone="danger">{t(authErrorKey(error))}</Alert>
      ) : search.expired ? (
        <Alert tone="info">{t("auth.login.expired")}</Alert>
      ) : search.reset ? (
        <Alert tone="success">{t("auth.login.passwordSet")}</Alert>
      ) : null}
      <Form {...form}>
        <form
          noValidate
          className="grid gap-4"
          onSubmit={(event) => void form.handleSubmit(onSubmit)(event)}
        >
          <FormField
            control={form.control}
            name="login"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("auth.login.loginLabel")}</FormLabel>
                <FormControl>
                  <Input
                    autoComplete="username"
                    autoCapitalize="off"
                    spellCheck={false}
                    dir="ltr"
                    autoFocus
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="password"
            render={({ field }) => (
              <FormItem>
                <div className="flex items-center justify-between gap-2">
                  <FormLabel>{t("auth.login.passwordLabel")}</FormLabel>
                  <Link
                    to="/forgot-password"
                    className="rounded-badge text-xs font-medium text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {t("auth.login.forgot")}
                  </Link>
                </div>
                <FormControl>
                  <PasswordInput autoComplete="current-password" dir="ltr" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button type="submit" size="lg" className="mt-1 w-full" pending={submitting}>
            {submitting ? t("auth.login.submitting") : t("auth.login.submit")}
          </Button>
        </form>
      </Form>
    </AuthCard>
  );
}

/** Ask for a reset link by email. The answer is the same whether the account exists or not. */
export function ForgotPasswordPage() {
  const { t } = useTranslation();
  usePageTitle(t("auth.forgot.title"));
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const forgot = useAuthPasswordForgot();
  const form = useForm<ForgotValues>({
    resolver: zodResolver(forgotSchema),
    defaultValues: { login: "" },
  });

  async function onSubmit(values: ForgotValues): Promise<void> {
    setError(null);
    try {
      await forgot.mutateAsync({ data: values });
      setSent(true);
    } catch (failure) {
      setError(failure);
    }
  }

  const back = (
    <Link to="/login" className="font-medium text-primary hover:underline">
      {t("auth.forgot.back")}
    </Link>
  );

  if (sent) {
    return (
      <AuthCard title={t("auth.forgot.sentTitle")} footer={back}>
        <div className="flex flex-col items-center gap-3 text-center">
          <MailCheck aria-hidden="true" className="size-8 text-primary" />
          <p className="text-sm text-muted-foreground" role="status">
            {t("auth.forgot.sent")}
          </p>
        </div>
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("auth.forgot.title")} subtitle={t("auth.forgot.subtitle")} footer={back}>
      {error !== null ? <Alert tone="danger">{t(authErrorKey(error))}</Alert> : null}
      <Form {...form}>
        <form
          noValidate
          className="grid gap-4"
          onSubmit={(event) => void form.handleSubmit(onSubmit)(event)}
        >
          <FormField
            control={form.control}
            name="login"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("auth.login.loginLabel")}</FormLabel>
                <FormControl>
                  <Input
                    autoComplete="username"
                    autoCapitalize="off"
                    spellCheck={false}
                    dir="ltr"
                    autoFocus
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button type="submit" className="w-full" pending={form.formState.isSubmitting}>
            {t("auth.forgot.submit")}
          </Button>
        </form>
      </Form>
    </AuthCard>
  );
}

/**
 * Set a password from an emailed link: a reset (`?uid=&token=`) or an invitation
 * from the admin (`&welcome=1`). The link works once.
 */
export function ResetPasswordPage() {
  const { t } = useTranslation();
  const search = resetApi.useSearch();
  const welcome = search.welcome === true;
  usePageTitle(welcome ? t("auth.reset.welcomeTitle") : t("auth.reset.title"));
  const navigate = useNavigate();
  const reset = useAuthPasswordReset();
  const [badLink, setBadLink] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const form = useForm<NewPasswordValues>({
    resolver: zodResolver(newPasswordSchema),
    defaultValues: { password: "", confirm: "" },
  });
  const { uid, token } = search;

  if (uid === undefined || token === undefined || badLink) {
    return (
      <AuthCard
        title={t("auth.reset.badLinkTitle")}
        footer={
          <Link to="/login" className="font-medium text-primary hover:underline">
            {t("auth.forgot.back")}
          </Link>
        }
      >
        <p className="text-sm text-muted-foreground">{t("auth.reset.badLink")}</p>
        <Button asChild>
          <Link to="/forgot-password">{t("auth.reset.newLink")}</Link>
        </Button>
      </AuthCard>
    );
  }

  async function onSubmit(values: NewPasswordValues): Promise<void> {
    if (uid === undefined || token === undefined) return;
    setError(null);
    try {
      await reset.mutateAsync({ data: { uid, token, password: values.password } });
      await navigate({ to: "/login", search: { reset: true }, replace: true });
    } catch (failure) {
      const keys = resetErrorKeys(failure);
      if (keys.link) {
        setBadLink(true);
      } else if (keys.password !== null) {
        form.setError(
          "password",
          { type: "validate", message: keys.password },
          { shouldFocus: true },
        );
      } else {
        setError(failure);
      }
    }
  }

  return (
    <AuthCard
      title={welcome ? t("auth.reset.welcomeTitle") : t("auth.reset.title")}
      subtitle={
        welcome
          ? t("auth.reset.welcomeSubtitle")
          : t("auth.reset.subtitle", { min: PASSWORD_MIN_LENGTH })
      }
    >
      {error !== null ? (
        <Alert tone="danger">
          {isApiError(error) && error.code === "RATE_LIMITED"
            ? t("auth.errors.RATE_LIMITED")
            : t(authErrorKey(error))}
        </Alert>
      ) : null}
      <Form {...form}>
        <form
          noValidate
          className="grid gap-4"
          onSubmit={(event) => void form.handleSubmit(onSubmit)(event)}
        >
          <FormField
            control={form.control}
            name="password"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("auth.reset.password")}</FormLabel>
                <FormControl>
                  <PasswordInput autoComplete="new-password" dir="ltr" autoFocus {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="confirm"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("auth.reset.confirm")}</FormLabel>
                <FormControl>
                  <PasswordInput autoComplete="new-password" dir="ltr" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button type="submit" className="w-full" pending={form.formState.isSubmitting}>
            {welcome ? t("auth.reset.welcomeSubmit") : t("auth.reset.submit")}
          </Button>
        </form>
      </Form>
    </AuthCard>
  );
}
