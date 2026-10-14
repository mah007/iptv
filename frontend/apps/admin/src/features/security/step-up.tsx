import {
  authStepUp,
  authStepUpForget,
  authStepUpOptions,
  isApiError,
  setStepUpHandler,
} from "@smart-iptv/api";
import {
  Alert,
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
  useNow,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, LockKeyhole, ShieldCheck } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { meQueryOptions, useMe } from "../../lib/auth";
import { notifyError } from "../../lib/problems";
import { keyCancelled, keysSupported, signWithKey } from "./webauthn";

type Resolve = (confirmed: boolean) => void;

function failureKey(error: unknown): string {
  if (keyCancelled(error)) return "stepUp.errors.cancelled";
  if (isApiError(error)) {
    if (error.code === "MFA_INVALID") return "stepUp.errors.invalid";
    if (error.code === "RATE_LIMITED") return "stepUp.errors.locked";
  }
  return "stepUp.errors.unavailable";
}

/**
 * Asks "Confirm it's you" when the API answers STEP_UP_REQUIRED (ADR-0021), with an
 * authenticator code or a security key, then lets the request through again. Mounted once
 * in the signed-in shell.
 */
export function StepUpProvider() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const me = useMe();
  const [resolve, setResolve] = useState<Resolve | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const codeId = useId();
  const methods = me?.mfa_methods ?? ["totp"];
  const withKey = methods.includes("webauthn") && keysSupported();
  const withCode = methods.includes("totp");

  useEffect(() => {
    setStepUpHandler(
      () =>
        new Promise<boolean>((done) => {
          setCode("");
          setError(null);
          setResolve(() => done);
        }),
    );
    return () => {
      setStepUpHandler(null);
    };
  }, []);

  function finish(confirmed: boolean): void {
    resolve?.(confirmed);
    setResolve(null);
    if (confirmed) void queryClient.invalidateQueries({ queryKey: meQueryOptions().queryKey });
  }

  async function confirm(run: () => Promise<unknown>): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await run();
      finish(true);
    } catch (failure) {
      if (isApiError(failure) && failure.code === "NOT_AUTHENTICATED") {
        finish(false); // too many tries: the session ended, sign-in follows
        return;
      }
      setError(failureKey(failure));
    } finally {
      setBusy(false);
    }
  }

  const confirmWithKey = () =>
    confirm(async () => {
      const { options } = await authStepUpOptions();
      await authStepUp({ credential: await signWithKey(options) });
    });
  const confirmWithCode = () =>
    confirm(async () => {
      await authStepUp({ code: code.trim() });
    });

  return (
    <Dialog
      open={resolve !== null}
      onOpenChange={(open) => {
        if (!open) finish(false);
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" className="size-5 text-primary" />
            {t("stepUp.title")}
          </DialogTitle>
          <DialogDescription>
            {t("stepUp.description", { minutes: me?.step_up_window_min ?? 5 })}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          {error ? <Alert tone="danger">{t(error)}</Alert> : null}
          {withKey ? (
            <Button
              type="button"
              variant={withCode ? "secondary" : "primary"}
              pending={busy}
              onClick={() => void confirmWithKey()}
            >
              <KeyRound aria-hidden="true" />
              {t("stepUp.useKey")}
            </Button>
          ) : null}
          {withCode ? (
            <form
              className="grid gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void confirmWithCode();
              }}
            >
              <Label htmlFor={codeId}>{t("stepUp.codeLabel")}</Label>
              <Input
                id={codeId}
                inputMode="numeric"
                autoComplete="one-time-code"
                autoFocus={!withKey}
                maxLength={7}
                dir="ltr"
                placeholder="000000"
                className="text-center font-mono tracking-[0.3em]"
                value={code}
                onChange={(event) => {
                  setCode(event.target.value);
                }}
              />
              <DialogFooter>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    finish(false);
                  }}
                >
                  {t("stepUp.cancel")}
                </Button>
                <Button type="submit" pending={busy} disabled={code.trim().length < 6}>
                  {t("stepUp.confirm")}
                </Button>
              </DialogFooter>
            </form>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** In the topbar while sensitive actions are unlocked: how long, and a Lock button. */
export function StepUpStatus() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const me = useMe();
  const now = useNow(1000);
  const until = me?.step_up_until ? Date.parse(me.step_up_until) : 0;
  const left = Math.max(0, Math.round((until - now) / 1000));
  if (left <= 0) return null;
  const minutes = Math.floor(left / 60);
  const seconds = String(left % 60).padStart(2, "0");
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="hidden gap-1.5 sm:inline-flex"
          aria-label={t("stepUp.lockNow")}
          onClick={() => {
            authStepUpForget().then(
              () => queryClient.invalidateQueries({ queryKey: meQueryOptions().queryKey }),
              (error: unknown) => {
                notifyError(t, error);
              },
            );
          }}
        >
          <LockKeyhole aria-hidden="true" />
          <Badge tone="warning">
            <span dir="ltr" className="tabular-nums">{`${String(minutes)}:${seconds}`}</span>
          </Badge>
        </Button>
      </TooltipTrigger>
      <TooltipContent>{t("stepUp.unlockedHint")}</TooltipContent>
    </Tooltip>
  );
}
