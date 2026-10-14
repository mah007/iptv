import { authStepUp, isApiError, setStepUpHandler } from "@smart-iptv/api-portal";
import {
  Alert,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Label,
  PasswordInput,
} from "@smart-iptv/ui";
import { ShieldCheck } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";

type Resolve = (confirmed: boolean) => void;

function failureKey(error: unknown): string {
  if (isApiError(error)) {
    if (error.code === "INVALID_CREDENTIALS") return "stepUp.errors.INVALID_CREDENTIALS";
    if (error.code === "ACCOUNT_LOCKED" || error.code === "RATE_LIMITED") {
      return "stepUp.errors.RATE_LIMITED";
    }
  }
  return "stepUp.errors.UNEXPECTED";
}

/**
 * "Confirm your password" before adding a TV app or a new TV app login (G-31, ADR-0021):
 * the API answers STEP_UP_REQUIRED when the last confirmation is older than the window,
 * this asks, and the request goes through again. Mounted once in the signed-in shell.
 */
export function StepUpDialog() {
  const { t } = useTranslation();
  const [resolve, setResolve] = useState<Resolve | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const passwordId = useId();

  useEffect(() => {
    setStepUpHandler(
      () =>
        new Promise<boolean>((done) => {
          setPassword("");
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
    setPassword("");
  }

  async function confirm(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await authStepUp({ password });
      finish(true);
    } catch (failure) {
      if (isApiError(failure) && failure.code === "NOT_AUTHENTICATED") {
        finish(false);
        return;
      }
      setError(failureKey(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={resolve !== null}
      onOpenChange={(open) => {
        if (!open) finish(false);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (password !== "") void confirm();
          }}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ShieldCheck aria-hidden="true" className="size-5 text-primary" />
              {t("stepUp.title")}
            </DialogTitle>
            <DialogDescription>{t("stepUp.description")}</DialogDescription>
          </DialogHeader>
          {error ? <Alert tone="danger">{t(error)}</Alert> : null}
          <div className="grid gap-1.5">
            <Label htmlFor={passwordId}>{t("stepUp.password")}</Label>
            <PasswordInput
              id={passwordId}
              autoComplete="current-password"
              dir="ltr"
              autoFocus
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
              }}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                finish(false);
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button type="submit" pending={busy} disabled={password === ""}>
              {t("stepUp.confirm")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
