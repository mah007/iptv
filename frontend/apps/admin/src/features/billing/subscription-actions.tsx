import {
  getCustomersRetrieveQueryKey,
  getDashboardBillingQueryKey,
  getSubscriptionsListQueryKey,
  useSubscriptionsApproveTrial,
  useSubscriptionsCancel,
  useSubscriptionsChangePlan,
  useSubscriptionsExtend,
  useSubscriptionsResume,
  useSubscriptionsSuspend,
  type Subscription,
} from "@smart-iptv/api";
import {
  Button,
  ConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Input,
  Label,
  Textarea,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftRight,
  CalendarPlus,
  CircleCheck,
  CircleSlash,
  MoreHorizontal,
  Pause,
  Play,
} from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { useCan } from "../../lib/auth";
import { notifyError, translatedFieldErrors } from "../../lib/problems";
import { PlanSelect, planName } from "./pickers";

/** Statuses a subscription can still be changed in. */
const LIVE = new Set(["pending", "active", "grace", "suspended"]);
const EXTEND_PRESETS = [7, 30, 90] as const;

type Action = "extend" | "change" | "cancel" | "suspend" | null;

/** Refresh everything a subscription change shows up in. */
export function useBillingRefresh() {
  const queryClient = useQueryClient();
  return async (customerId?: string) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: getSubscriptionsListQueryKey() }),
      queryClient.invalidateQueries({ queryKey: getDashboardBillingQueryKey() }),
      ...(customerId
        ? [queryClient.invalidateQueries({ queryKey: getCustomersRetrieveQueryKey(customerId) })]
        : []),
    ]);
  };
}

function ExtendDialog({
  subscription,
  open,
  onOpenChange,
}: {
  subscription: Subscription;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const id = useId();
  const refresh = useBillingRefresh();
  const extend = useSubscriptionsExtend();
  const [days, setDays] = useState("30");
  const [error, setError] = useState<string | null>(null);
  const value = Number(days);
  const valid = /^\d+$/u.test(days) && value >= 1 && value <= 3660;
  const newEnd = valid
    ? new Date(Date.parse(subscription.ends_at) + value * 86_400_000).toISOString()
    : null;

  async function submit(): Promise<void> {
    setError(null);
    try {
      await extend.mutateAsync({ id: subscription.id, data: { days: value } });
      await refresh(subscription.user.id);
      toast.success(t("subscriptions.extend.done", { count: value }));
      onOpenChange(false);
    } catch (failure) {
      const messages = translatedFieldErrors(t, failure);
      if (messages[0]) setError(messages[0]);
      else notifyError(t, failure);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("subscriptions.extend.title")}</DialogTitle>
          <DialogDescription>
            {t("subscriptions.extend.description", {
              name: subscription.user.name || subscription.user.username,
              date: format.date(subscription.ends_at),
            })}
          </DialogDescription>
        </DialogHeader>
        <form
          noValidate
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid) void submit();
          }}
        >
          <div className="grid gap-1.5">
            <Label htmlFor={id}>{t("subscriptions.extend.days")}</Label>
            <div className="flex flex-wrap items-center gap-2">
              <Input
                id={id}
                inputMode="numeric"
                dir="ltr"
                className="w-28 tabular-nums"
                value={days}
                aria-invalid={!valid || error !== null ? true : undefined}
                aria-describedby={`${id}-help`}
                onChange={(event) => {
                  setDays(event.target.value);
                }}
              />
              {EXTEND_PRESETS.map((preset) => (
                <Button
                  key={preset}
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setDays(String(preset));
                  }}
                >
                  {t("subscriptions.extend.preset", { count: preset })}
                </Button>
              ))}
            </div>
            <p id={`${id}-help`} className="text-xs text-muted-foreground">
              {newEnd
                ? t("subscriptions.extend.newEnd", { date: format.date(newEnd) })
                : t("subscriptions.extend.range")}
            </p>
            {error ? <p className="text-xs font-medium text-danger-text">{error}</p> : null}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                onOpenChange(false);
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={!valid} pending={extend.isPending}>
              {t("subscriptions.extend.submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ChangePlanDialog({
  subscription,
  open,
  onOpenChange,
}: {
  subscription: Subscription;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t, i18n } = useTranslation();
  const id = useId();
  const refresh = useBillingRefresh();
  const change = useSubscriptionsChangePlan();
  const [plan, setPlan] = useState("");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("subscriptions.change.title")}</DialogTitle>
          <DialogDescription>
            {t("subscriptions.change.description", {
              plan: planName(subscription.plan, i18n.language),
            })}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-1.5">
          <Label htmlFor={id}>{t("subscriptions.change.plan")}</Label>
          <PlanSelect id={id} value={plan} onChange={setPlan} exclude={subscription.plan.id} />
        </div>
        <DialogFooter>
          <Button
            variant="secondary"
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("common.cancel")}
          </Button>
          <Button
            disabled={plan === ""}
            pending={change.isPending}
            onClick={() => {
              change.mutate(
                { id: subscription.id, data: { plan_id: plan } },
                {
                  onSuccess: () => {
                    void refresh(subscription.user.id);
                    toast.success(t("subscriptions.change.done"));
                    onOpenChange(false);
                  },
                  onError: (error) => {
                    notifyError(t, error);
                  },
                },
              );
            }}
          >
            {t("subscriptions.change.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ReasonDialog({
  subscription,
  kind,
  open,
  onOpenChange,
}: {
  subscription: Subscription;
  kind: "cancel" | "suspend";
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const refresh = useBillingRefresh();
  const cancel = useSubscriptionsCancel();
  const suspend = useSubscriptionsSuspend();
  const [reason, setReason] = useState("");
  const pending = cancel.isPending || suspend.isPending;
  const name = subscription.user.name || subscription.user.username;

  async function submit(): Promise<void> {
    try {
      const data = { reason: reason.trim() };
      if (kind === "cancel") await cancel.mutateAsync({ id: subscription.id, data });
      else await suspend.mutateAsync({ id: subscription.id, data });
      await refresh(subscription.user.id);
      toast.success(t(`subscriptions.${kind}.done`, { name }));
      onOpenChange(false);
    } catch (error) {
      notifyError(t, error);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent role="alertdialog">
        <DialogHeader>
          <DialogTitle>{t(`subscriptions.${kind}.title`, { name })}</DialogTitle>
          <DialogDescription>{t(`subscriptions.${kind}.description`)}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-1.5">
          <Label htmlFor={id}>{t("subscriptions.reason")}</Label>
          <Textarea
            id={id}
            dir="auto"
            maxLength={200}
            value={reason}
            onChange={(event) => {
              setReason(event.target.value);
            }}
          />
        </div>
        <DialogFooter>
          <Button
            variant="secondary"
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("common.cancel")}
          </Button>
          <Button
            variant={kind === "cancel" ? "danger" : "primary"}
            pending={pending}
            onClick={() => {
              void submit();
            }}
          >
            {t(`subscriptions.${kind}.submit`)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * The actions of one subscription (SPEC §8.3.13): extend, change plan,
 * suspend or resume, cancel, and approving a requested trial.
 */
export function SubscriptionActions({ subscription }: { subscription: Subscription }) {
  const { t } = useTranslation();
  const can = useCan();
  const refresh = useBillingRefresh();
  const resume = useSubscriptionsResume();
  const approve = useSubscriptionsApproveTrial();
  const [action, setAction] = useState<Action>(null);
  const [approving, setApproving] = useState(false);
  if (!can("subscriptions.edit") || !LIVE.has(subscription.status)) return null;
  const name = subscription.user.name || subscription.user.username;
  const isTrialRequest = subscription.status === "pending" && subscription.source === "trial";

  function dialog(kind: Exclude<Action, null>) {
    return {
      open: action === kind,
      onOpenChange: (open: boolean) => {
        setAction(open ? kind : null);
      },
    };
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t("subscriptions.actions.label", { name })}
          >
            <MoreHorizontal aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {isTrialRequest ? (
            <DropdownMenuItem
              onSelect={() => {
                setApproving(true);
              }}
            >
              <CircleCheck aria-hidden="true" />
              {t("subscriptions.approve.action")}
            </DropdownMenuItem>
          ) : null}
          {subscription.status !== "pending" ? (
            <DropdownMenuItem
              onSelect={() => {
                setAction("extend");
              }}
            >
              <CalendarPlus aria-hidden="true" />
              {t("subscriptions.extend.action")}
            </DropdownMenuItem>
          ) : null}
          {subscription.status !== "pending" ? (
            <DropdownMenuItem
              onSelect={() => {
                setAction("change");
              }}
            >
              <ArrowLeftRight aria-hidden="true" />
              {t("subscriptions.change.action")}
            </DropdownMenuItem>
          ) : null}
          {subscription.status === "suspended" ? (
            <DropdownMenuItem
              onSelect={() => {
                resume.mutate(
                  { id: subscription.id },
                  {
                    onSuccess: () => {
                      void refresh(subscription.user.id);
                      toast.success(t("subscriptions.resume.done", { name }));
                    },
                    onError: (error) => {
                      notifyError(t, error);
                    },
                  },
                );
              }}
            >
              <Play aria-hidden="true" className="rtl:-scale-x-100" />
              {t("subscriptions.resume.action")}
            </DropdownMenuItem>
          ) : subscription.status !== "pending" ? (
            <DropdownMenuItem
              onSelect={() => {
                setAction("suspend");
              }}
            >
              <Pause aria-hidden="true" />
              {t("subscriptions.suspend.action")}
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            className="text-danger-text"
            onSelect={() => {
              setAction("cancel");
            }}
          >
            <CircleSlash aria-hidden="true" />
            {t("subscriptions.cancel.action")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {action === "extend" ? (
        <ExtendDialog subscription={subscription} {...dialog("extend")} />
      ) : null}
      {action === "change" ? (
        <ChangePlanDialog subscription={subscription} {...dialog("change")} />
      ) : null}
      {action === "cancel" || action === "suspend" ? (
        <ReasonDialog subscription={subscription} kind={action} {...dialog(action)} />
      ) : null}
      <ConfirmDialog
        open={approving}
        onOpenChange={setApproving}
        title={t("subscriptions.approve.title", { name })}
        description={t("subscriptions.approve.description")}
        confirmLabel={t("subscriptions.approve.action")}
        onConfirm={async () => {
          try {
            await approve.mutateAsync({ id: subscription.id });
            await refresh(subscription.user.id);
            toast.success(t("subscriptions.approve.done", { name }));
          } catch (error) {
            notifyError(t, error);
            throw error;
          }
        }}
      />
    </>
  );
}
