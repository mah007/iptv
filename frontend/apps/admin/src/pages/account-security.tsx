import {
  authStepUpForget,
  authWebauthnCredentialsCreate,
  authWebauthnCredentialsOptions,
  getAuthWebauthnCredentialsListQueryKey,
  isApiError,
  useAuthPasswordChange,
  useAuthWebauthnCredentialsDestroy,
  useAuthWebauthnCredentialsList,
  useAuthWebauthnCredentialsPartialUpdate,
  type WebAuthnCredential,
} from "@smart-iptv/api";
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  Label,
  PageHeader,
  Skeleton,
  toast,
  useFormatters,
  useNow,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, KeySquare, LockKeyhole, Plus, ShieldCheck, Smartphone } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError } from "../components/states";
import { keyCancelled, keysSupported, createKey } from "../features/security/webauthn";
import { meQueryOptions, useMe } from "../lib/auth";
import { fieldErrorCodes, fieldErrorMessage, notifyError } from "../lib/problems";
import { usePageTitle } from "../lib/page-title";

function AddKeyDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const nameId = useId();

  async function add(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const { options } = await authWebauthnCredentialsOptions();
      const credential = await createKey(options);
      await authWebauthnCredentialsCreate({
        name: name.trim() || t("security.keys.defaultName"),
        credential,
      });
      await queryClient.invalidateQueries({ queryKey: getAuthWebauthnCredentialsListQueryKey() });
      await queryClient.invalidateQueries({ queryKey: meQueryOptions().queryKey });
      toast.success(t("security.keys.added"));
      setName("");
      onOpenChange(false);
    } catch (failure) {
      if (keyCancelled(failure)) setError("security.keys.cancelled");
      else if (isApiError(failure) && failure.code === "CONFLICT")
        setError("security.keys.duplicate");
      else if (isApiError(failure) && failure.code === "MFA_INVALID")
        setError("security.keys.invalid");
      else if (!(isApiError(failure) && failure.code === "STEP_UP_REQUIRED")) {
        setError("security.keys.failed");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("security.keys.addTitle")}</DialogTitle>
          <DialogDescription>{t("security.keys.addDescription")}</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void add();
          }}
        >
          {error ? <Alert tone="danger">{t(error)}</Alert> : null}
          <Label htmlFor={nameId}>{t("security.keys.nameLabel")}</Label>
          <Input
            id={nameId}
            maxLength={64}
            placeholder={t("security.keys.namePlaceholder")}
            value={name}
            onChange={(event) => {
              setName(event.target.value);
            }}
          />
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                onOpenChange(false);
              }}
            >
              {t("security.cancel")}
            </Button>
            <Button type="submit" pending={busy}>
              <KeyRound aria-hidden="true" />
              {t("security.keys.addSubmit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function KeyRow({ item }: { item: WebAuthnCredential }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const queryClient = useQueryClient();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(item.name);
  const nameId = useId();
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: getAuthWebauthnCredentialsListQueryKey() });
  const rename = useAuthWebauthnCredentialsPartialUpdate({
    mutation: {
      onSuccess: async () => {
        setRenaming(false);
        await refresh();
      },
      onError: (error) => {
        notifyError(t, error);
      },
    },
  });
  const remove = useAuthWebauthnCredentialsDestroy({
    mutation: {
      onSuccess: async () => {
        toast.success(t("security.keys.removed"));
        await refresh();
        await queryClient.invalidateQueries({ queryKey: meQueryOptions().queryKey });
      },
      onError: (error) => {
        notifyError(t, error);
      },
    },
  });
  return (
    <li className="flex flex-wrap items-center gap-3 py-3">
      <span className="grid size-9 place-items-center rounded-full bg-muted text-muted-foreground">
        {item.multi_device ? (
          <Smartphone aria-hidden="true" className="size-4" />
        ) : (
          <KeyRound aria-hidden="true" className="size-4" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        {renaming ? (
          <form
            className="flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              rename.mutate({ id: item.id, data: { name } });
            }}
          >
            <Label htmlFor={nameId} className="sr-only">
              {t("security.keys.nameLabel")}
            </Label>
            <Input
              id={nameId}
              value={name}
              maxLength={64}
              onChange={(event) => {
                setName(event.target.value);
              }}
              className="h-8 max-w-xs"
            />
            <Button type="submit" size="sm" pending={rename.isPending}>
              {t("security.save")}
            </Button>
          </form>
        ) : (
          <p className="truncate text-ui font-medium text-foreground">{item.name}</p>
        )}
        <p className="text-xs text-muted-foreground">
          {t("security.keys.addedOn", { date: format.date(item.created_at) })}
          {" · "}
          {item.last_used_at
            ? t("security.keys.lastUsed", { date: format.dateTime(item.last_used_at) })
            : t("security.keys.neverUsed")}
        </p>
      </div>
      {item.multi_device ? <Badge tone="info">{t("security.keys.passkey")}</Badge> : null}
      {!renaming ? (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setRenaming(true);
          }}
        >
          {t("security.keys.rename")}
        </Button>
      ) : null}
      <ConfirmDialog
        tone="danger"
        title={t("security.keys.removeTitle", { name: item.name })}
        description={t("security.keys.removeDescription")}
        confirmLabel={t("security.keys.remove")}
        cancelLabel={t("security.cancel")}
        onConfirm={async () => {
          await remove.mutateAsync({ id: item.id });
        }}
        trigger={
          <Button variant="ghost" size="sm" className="text-danger-text">
            {t("security.keys.remove")}
          </Button>
        }
      />
    </li>
  );
}

/** My account → Security: the admin's sign-in confirmation (ADR-0021). */
/** My password (ADR-0023): the current one, a code or key (asked by the API), the new one. */
function PasswordCard() {
  const { t } = useTranslation();
  const change = useAuthPasswordChange();
  const currentId = useId();
  const newId = useId();
  const repeatId = useId();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const mismatch = repeat !== "" && repeat !== next;

  function fieldError(field: string): string | undefined {
    return errors[field];
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeySquare aria-hidden="true" className="size-4 text-muted-foreground" />
          {t("security.password.title")}
        </CardTitle>
        <CardDescription>{t("security.password.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="grid max-w-md gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (mismatch || current === "" || next === "") return;
            setErrors({});
            change.mutate(
              { data: { current_password: current, new_password: next } },
              {
                onSuccess: () => {
                  toast.success(t("security.password.changed"));
                  setCurrent("");
                  setNext("");
                  setRepeat("");
                },
                onError: (error) => {
                  if (isApiError(error) && error.code === "VALIDATION_ERROR") {
                    const found: Record<string, string> = {};
                    for (const [field, messages] of Object.entries(error.fieldErrors)) {
                      const codes = fieldErrorCodes(error, field);
                      const first = messages[0];
                      if (first !== undefined) {
                        found[field] = fieldErrorMessage(t, codes[0], first);
                      }
                    }
                    setErrors(found);
                    return;
                  }
                  notifyError(t, error);
                },
              },
            );
          }}
        >
          <div className="grid gap-1.5">
            <Label htmlFor={currentId}>{t("security.password.current")}</Label>
            <Input
              id={currentId}
              type="password"
              autoComplete="current-password"
              value={current}
              aria-invalid={fieldError("current_password") ? true : undefined}
              aria-describedby={fieldError("current_password") ? `${currentId}-error` : undefined}
              onChange={(event) => {
                setCurrent(event.target.value);
              }}
            />
            {fieldError("current_password") ? (
              <p id={`${currentId}-error`} className="text-xs text-danger-text">
                {fieldError("current_password")}
              </p>
            ) : null}
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={newId}>{t("security.password.new")}</Label>
            <Input
              id={newId}
              type="password"
              autoComplete="new-password"
              value={next}
              aria-invalid={fieldError("new_password") ? true : undefined}
              aria-describedby={`${newId}-help`}
              onChange={(event) => {
                setNext(event.target.value);
              }}
            />
            <p id={`${newId}-help`} className="text-xs text-muted-foreground">
              {fieldError("new_password") ?? t("security.password.rules")}
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={repeatId}>{t("security.password.repeat")}</Label>
            <Input
              id={repeatId}
              type="password"
              autoComplete="new-password"
              value={repeat}
              aria-invalid={mismatch ? true : undefined}
              aria-describedby={mismatch ? `${repeatId}-error` : undefined}
              onChange={(event) => {
                setRepeat(event.target.value);
              }}
            />
            {mismatch ? (
              <p id={`${repeatId}-error`} className="text-xs text-danger-text">
                {t("security.password.mismatch")}
              </p>
            ) : null}
          </div>
          <div>
            <Button
              type="submit"
              pending={change.isPending}
              disabled={current === "" || next === "" || repeat === "" || mismatch}
            >
              {t("security.password.submit")}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

export function AccountSecurityPage() {
  const { t } = useTranslation();
  usePageTitle(t("security.title"));
  const me = useMe();
  const queryClient = useQueryClient();
  const keys = useAuthWebauthnCredentialsList();
  const [adding, setAdding] = useState(false);
  const supported = keysSupported();
  const now = useNow(1000);
  const unlocked = me?.step_up_until ? Date.parse(me.step_up_until) > now : false;

  return (
    <div className="grid gap-(--density-page)">
      <PageHeader title={t("security.title")} description={t("security.description")} />
      {me?.webauthn_enrolment_required ? (
        <Alert tone="warning" title={t("security.enrolRequired.title")}>
          {t("security.enrolRequired.description")}
        </Alert>
      ) : null}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound aria-hidden="true" className="size-4 text-muted-foreground" />
            {t("security.keys.title")}
          </CardTitle>
          <CardDescription>{t("security.keys.description")}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          {!supported ? <Alert tone="info">{t("security.keys.unsupported")}</Alert> : null}
          {keys.isPending ? (
            <Skeleton className="h-16 w-full" />
          ) : keys.isError ? (
            <QueryError error={keys.error} />
          ) : keys.data.length === 0 ? (
            <EmptyState
              icon={<KeyRound />}
              title={t("security.keys.emptyTitle")}
              description={t("security.keys.emptyDescription")}
            />
          ) : (
            <ul className="divide-y divide-border">
              {keys.data.map((item) => (
                <KeyRow key={item.id} item={item} />
              ))}
            </ul>
          )}
          <div>
            <Button
              disabled={!supported}
              onClick={() => {
                setAdding(true);
              }}
            >
              <Plus aria-hidden="true" />
              {t("security.keys.add")}
            </Button>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" className="size-4 text-muted-foreground" />
            {t("security.totp.title")}
          </CardTitle>
          <CardDescription>{t("security.totp.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <Badge tone={me?.mfa_methods.includes("totp") ? "success" : "neutral"} dot>
            {me?.mfa_methods.includes("totp") ? t("security.totp.on") : t("security.totp.keysOnly")}
          </Badge>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <LockKeyhole aria-hidden="true" className="size-4 text-muted-foreground" />
            {t("security.stepUp.title")}
          </CardTitle>
          <CardDescription>
            {t("security.stepUp.description", { minutes: me?.step_up_window_min ?? 5 })}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            variant="secondary"
            disabled={!unlocked}
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
            {unlocked ? t("security.stepUp.lock") : t("security.stepUp.locked")}
          </Button>
        </CardContent>
      </Card>
      <PasswordCard />
      <AddKeyDialog open={adding} onOpenChange={setAdding} />
    </div>
  );
}
