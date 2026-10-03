import { zodResolver } from "@hookform/resolvers/zod";
import {
  Alert,
  Button,
  CopyField,
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  QRCodeCard,
} from "@smart-iptv/ui";
import { ArrowLeft, ShieldCheck } from "lucide-react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { authErrorKey } from "./errors";
import { mfaCodeSchema, otpauthSecret, type MfaCodeInput, type MfaCodeValues } from "./schemas";

export type MfaFormProps = {
  /** Called with the six-digit code; a returned promise keeps the button busy. */
  onSubmit: (values: MfaCodeValues) => void | Promise<void>;
  /** Abandon this sign-in and return to the password step. */
  onBack: () => void;
  /** problem+json code of the last failed attempt, e.g. MFA_INVALID. */
  errorCode?: string | null;
} & (
  | { mode: "verify" }
  | {
      /** First sign-in: enrol an authenticator app from the login response's otpauth URI. */
      mode: "setup";
      otpauthUri: string;
    }
);

/** Second sign-in step: verify a TOTP code, or enrol an authenticator first. Presentational. */
export function MfaForm(props: MfaFormProps) {
  const { onSubmit, onBack, errorCode = null } = props;
  const { t } = useTranslation();
  const form = useForm<MfaCodeInput, unknown, MfaCodeValues>({
    resolver: zodResolver(mfaCodeSchema),
    defaultValues: { code: "" },
  });
  const submitting = form.formState.isSubmitting;
  const setup = props.mode === "setup";
  const secret = setup ? otpauthSecret(props.otpauthUri) : null;

  return (
    <div className="grid gap-6">
      <div className="grid justify-items-center gap-1.5 text-center">
        <span className="mb-1 grid size-10 place-items-center rounded-full bg-primary/10 text-primary">
          <ShieldCheck aria-hidden="true" className="size-5" />
        </span>
        <h1 className="text-xl font-semibold text-foreground ltr:tracking-tight">
          {setup ? t("auth.mfa.setupTitle") : t("auth.mfa.verifyTitle")}
        </h1>
        <p className="text-ui text-muted-foreground">
          {setup ? t("auth.mfa.setupSubtitle") : t("auth.mfa.verifySubtitle")}
        </p>
      </div>

      <div className="grid gap-4 rounded-card border border-border bg-card p-6 shadow-elevation">
        {errorCode ? <Alert tone="danger">{t(authErrorKey(errorCode))}</Alert> : null}

        {setup ? (
          <ol className="grid gap-4">
            <li className="grid gap-3">
              <p className="text-ui text-foreground">
                <span className="me-1.5 font-semibold text-primary">{t("auth.mfa.step1")}</span>
                {t("auth.mfa.scan")}
              </p>
              <QRCodeCard
                value={props.otpauthUri}
                label={t("auth.mfa.qrLabel")}
                size={168}
                className="border-dashed shadow-none"
              />
              {secret ? <CopyField label={t("auth.mfa.manualKey")} value={secret} /> : null}
            </li>
            <li>
              <p className="text-ui text-foreground">
                <span className="me-1.5 font-semibold text-primary">{t("auth.mfa.step2")}</span>
                {t("auth.mfa.enterCode")}
              </p>
            </li>
          </ol>
        ) : null}

        <Form {...form}>
          <form
            noValidate
            className="grid gap-4"
            onSubmit={(event) => void form.handleSubmit(onSubmit)(event)}
          >
            <FormField
              control={form.control}
              name="code"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("auth.mfa.codeLabel")}</FormLabel>
                  <FormControl>
                    <Input
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      autoFocus={!setup}
                      maxLength={7}
                      dir="ltr"
                      placeholder="000000"
                      className="h-12 text-center indent-[0.4em] font-mono text-xl tracking-[0.4em] placeholder:tracking-[0.4em]"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>{t("auth.mfa.codeHelp")}</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button type="submit" className="w-full" pending={submitting}>
              {setup ? t("auth.mfa.submitSetup") : t("auth.mfa.submit")}
            </Button>
          </form>
        </Form>
      </div>

      <Button variant="ghost" size="sm" className="justify-self-center" onClick={onBack}>
        <ArrowLeft aria-hidden="true" className="rtl:-scale-x-100" />
        {t("auth.mfa.back")}
      </Button>
    </div>
  );
}
