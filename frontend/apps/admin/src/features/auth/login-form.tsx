import { zodResolver } from "@hookform/resolvers/zod";
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
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { authErrorKey } from "./errors";
import { loginSchema, type LoginInput, type LoginValues } from "./schemas";

export interface LoginFormProps {
  /** Called with validated values; a returned promise keeps the button busy. */
  onSubmit: (values: LoginValues) => void | Promise<void>;
  /** problem+json code of the last failed attempt, e.g. INVALID_CREDENTIALS. */
  errorCode?: string | null;
}

/** Staff sign-in (username or email + password). Presentational: the caller talks to the API. */
export function LoginForm({ onSubmit, errorCode = null }: LoginFormProps) {
  const { t } = useTranslation();
  const form = useForm<LoginInput, unknown, LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { login: "", password: "" },
  });
  const submitting = form.formState.isSubmitting;

  return (
    <div className="grid gap-6">
      <div className="grid gap-1.5 text-center">
        <h1 className="text-xl font-semibold text-foreground ltr:tracking-tight">
          {t("auth.login.title")}
        </h1>
        <p className="text-ui text-muted-foreground">{t("auth.login.subtitle")}</p>
      </div>

      <div className="grid gap-4 rounded-card border border-border bg-card p-6 shadow-elevation">
        {errorCode ? <Alert tone="danger">{t(authErrorKey(errorCode))}</Alert> : null}
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
                  <FormLabel>{t("auth.login.passwordLabel")}</FormLabel>
                  <FormControl>
                    <PasswordInput autoComplete="current-password" dir="ltr" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button type="submit" className="mt-1 w-full" pending={submitting}>
              {submitting ? t("auth.login.submitting") : t("auth.login.submit")}
            </Button>
          </form>
        </Form>
      </div>

      <p className="text-center text-xs text-muted-foreground">{t("auth.login.sessionNote")}</p>
    </div>
  );
}
