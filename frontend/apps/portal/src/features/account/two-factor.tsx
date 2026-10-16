import {
  isApiError,
  useMeMfaConfirm,
  useMeMfaRemove,
  useMeMfaSetup,
  type CustomerMe,
} from "@smart-iptv/api-portal";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  CopyField,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  QRCodeCard,
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Smartphone } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { meQueryOptions } from "../../lib/auth";

/** The key an authenticator app asks for when the QR code can't be scanned. */
export function otpauthSecret(uri: string): string {
  try {
    return new URL(uri).searchParams.get("secret") ?? "";
  } catch {
    return "";
  }
}

/** Six digits, spaces ignored (apps show "123 456"). */
export function normaliseCode(value: string): string {
  return value.replace(/\s+/gu, "");
}

function errorKey(error: unknown): string {
  if (isApiError(error)) {
    if (error.code === "MFA_INVALID") return "twoFactor.errors.MFA_INVALID";
    if (error.code === "RATE_LIMITED" || error.code === "ACCOUNT_LOCKED") {
      return "twoFactor.errors.RATE_LIMITED";
    }
  }
  return "twoFactor.errors.UNEXPECTED";
}

/**
 * Setting up two-factor sign-in (ADR-0024): the API makes a new secret (after the password
 * step-up), the app scans it, and its first code switches it on.
 */
function SetUpDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const setup = useMeMfaSetup();
  const confirm = useMeMfaConfirm();
  const codeId = useId();
  const [uri, setUri] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  function close(): void {
    setUri(null);
    setCode("");
    setError(null);
    onOpenChange(false);
  }

  async function start(): Promise<void> {
    setError(null);
    try {
      setUri((await setup.mutateAsync()).otpauth_uri);
    } catch (failure) {
      if (isApiError(failure) && failure.code === "STEP_UP_REQUIRED") {
        close();
        return;
      }
      setError(errorKey(failure));
    }
  }

  async function finish(): Promise<void> {
    setError(null);
    try {
      const me = await confirm.mutateAsync({ data: { code: normaliseCode(code) } });
      queryClient.setQueryData(meQueryOptions().queryKey, me);
      toast.success(t("twoFactor.enabled"));
      close();
    } catch (failure) {
      setError(errorKey(failure));
    }
  }

  const secret = uri === null ? "" : otpauthSecret(uri);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("twoFactor.setupTitle")}</DialogTitle>
          <DialogDescription>{t("twoFactor.setupDescription")}</DialogDescription>
        </DialogHeader>
        {error ? <Alert tone="danger">{t(error)}</Alert> : null}
        {uri === null ? (
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={close}>
              {t("twoFactor.cancel")}
            </Button>
            <Button type="button" pending={setup.isPending} onClick={() => void start()}>
              {t("twoFactor.begin")}
            </Button>
          </DialogFooter>
        ) : (
          <form
            className="grid gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (normaliseCode(code).length === 6) void finish();
            }}
          >
            <ol className="grid list-decimal gap-1 ps-5 text-ui text-muted-foreground">
              <li>{t("twoFactor.steps.install")}</li>
              <li>{t("twoFactor.steps.scan")}</li>
              <li>{t("twoFactor.steps.code")}</li>
            </ol>
            <div className="grid items-start gap-4 sm:grid-cols-[auto_minmax(0,1fr)]">
              <QRCodeCard value={uri} label={t("twoFactor.qrLabel")} size={144} />
              <div className="grid min-w-0 gap-3">
                <CopyField label={t("twoFactor.manualKey")} value={secret} />
                <div className="grid gap-1.5">
                  <Label htmlFor={codeId}>{t("twoFactor.code")}</Label>
                  <Input
                    id={codeId}
                    autoComplete="one-time-code"
                    inputMode="numeric"
                    dir="ltr"
                    maxLength={8}
                    value={code}
                    onChange={(event) => {
                      setCode(event.target.value);
                    }}
                  />
                </div>
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={close}>
                {t("twoFactor.cancel")}
              </Button>
              <Button
                type="submit"
                pending={confirm.isPending}
                disabled={normaliseCode(code).length !== 6}
              >
                {t("twoFactor.turnOn")}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

/**
 * Two-factor sign-in on the account page (ADR-0024): off by default; when on, every new
 * sign-in (here and in apps) asks for the authenticator app's code. Both changes need the
 * password again (step-up). A lost phone: support switches it off.
 */
export function TwoFactorCard({ me }: { me: CustomerMe }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const remove = useMeMfaRemove();
  const [setting, setSetting] = useState(false);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Smartphone aria-hidden="true" className="size-4" />
          {t("twoFactor.title")}
          <Badge tone={me.mfa_enabled ? "success" : "neutral"} dot className="ms-1">
            {me.mfa_enabled ? t("twoFactor.on") : t("twoFactor.off")}
          </Badge>
        </CardTitle>
        <CardDescription>
          {me.mfa_enabled ? t("twoFactor.onHint") : t("twoFactor.offHint")}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {me.mfa_enabled ? (
          <>
            <p className="text-ui text-muted-foreground">{t("twoFactor.lostPhone")}</p>
            <ConfirmDialog
              tone="danger"
              title={t("twoFactor.offTitle")}
              description={t("twoFactor.offDescription")}
              confirmLabel={t("twoFactor.turnOff")}
              cancelLabel={t("twoFactor.cancel")}
              onConfirm={async () => {
                try {
                  await remove.mutateAsync();
                } catch (failure) {
                  toast.error(t(errorKey(failure)));
                  throw failure;
                }
                queryClient.setQueryData(meQueryOptions().queryKey, { ...me, mfa_enabled: false });
                toast.success(t("twoFactor.disabled"));
              }}
              trigger={
                <Button variant="secondary" className="justify-self-start">
                  {t("twoFactor.turnOff")}
                </Button>
              }
            />
          </>
        ) : (
          <Button
            variant="secondary"
            className="justify-self-start"
            onClick={() => {
              setSetting(true);
            }}
          >
            {t("twoFactor.setUp")}
          </Button>
        )}
        <SetUpDialog open={setting} onOpenChange={setSetting} />
      </CardContent>
    </Card>
  );
}
