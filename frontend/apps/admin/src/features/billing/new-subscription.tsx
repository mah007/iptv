import { useSubscriptionsCreate, useSubscriptionsStartTrial, usePlansList } from "@smart-iptv/api";
import {
  Button,
  DEFAULT_TIME_ZONE,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Switch,
  Textarea,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { useMe } from "../../lib/auth";
import { notifyError, translatedFieldErrors } from "../../lib/problems";
import { addMonths, endOfDayIn, isoDateIn, startOfDayIn } from "../../lib/time";
import { CustomerPicker, PlanSelect, type PickedCustomer } from "./pickers";
import { useBillingRefresh } from "./subscription-actions";

/**
 * Activate a plan for a customer (SPEC §7.6 `subscriptions.activate`): it
 * snapshots the plan, rebuilds the customer's entitlement and notifies them.
 * A trial plan starts a free trial instead (its own limits apply).
 */
export function NewSubscriptionDialog({
  open,
  onOpenChange,
  customer,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Fixed on a customer's page; picked otherwise. */
  customer?: PickedCustomer | undefined;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const me = useMe();
  const timeZone = me?.timezone ?? DEFAULT_TIME_ZONE;
  const ids = { customer: useId(), plan: useId(), start: useId(), end: useId(), note: useId() };
  const refresh = useBillingRefresh();
  const create = useSubscriptionsCreate();
  const startTrial = useSubscriptionsStartTrial();
  const [picked, setPicked] = useState<PickedCustomer | null>(customer ?? null);
  const [trial, setTrial] = useState(false);
  const [planId, setPlanId] = useState("");
  const [start, setStart] = useState(() => isoDateIn(new Date(), timeZone));
  const [end, setEnd] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const plans = usePlansList({ active: true, is_trial: trial });
  const plan = plans.data?.find((candidate) => candidate.id === planId);
  const target = customer ?? picked;
  const pending = create.isPending || startTrial.isPending;

  // The end the API computes from the plan, shown before the admin overrides it.
  const computedEnd =
    plan && /^\d{4}-\d{2}-\d{2}$/u.test(start)
      ? (() => {
          const months = addMonths(start, plan.duration_months);
          const date = new Date(`${months}T00:00:00Z`);
          date.setUTCDate(date.getUTCDate() + plan.duration_days);
          return date.toISOString().slice(0, 10);
        })()
      : null;

  function reset(): void {
    setPicked(customer ?? null);
    setTrial(false);
    setPlanId("");
    setEnd("");
    setNote("");
    setError(null);
  }

  async function submit(): Promise<void> {
    if (target === null || planId === "") return;
    setError(null);
    try {
      if (trial) {
        await startTrial.mutateAsync({ data: { user_id: target.id, plan_id: planId } });
      } else {
        const today = isoDateIn(new Date(), timeZone);
        await create.mutateAsync({
          data: {
            user_id: target.id,
            plan_id: planId,
            source: "manual",
            starts_at: start === today ? null : startOfDayIn(start, timeZone),
            ends_at: end ? endOfDayIn(end, timeZone) : null,
            note: note.trim(),
          },
        });
      }
      await refresh(target.id);
      toast.success(
        t(trial ? "subscriptions.new.trialDone" : "subscriptions.new.done", { name: target.name }),
      );
      reset();
      onOpenChange(false);
    } catch (failure) {
      const messages = translatedFieldErrors(t, failure);
      if (messages[0]) setError(messages[0]);
      else notifyError(t, failure);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("subscriptions.new.title")}</DialogTitle>
          <DialogDescription>{t("subscriptions.new.description")}</DialogDescription>
        </DialogHeader>
        <form
          noValidate
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          {customer ? null : (
            <div className="grid gap-1.5">
              <Label htmlFor={ids.customer}>{t("subscriptions.fields.customer")}</Label>
              <CustomerPicker id={ids.customer} value={picked} onChange={setPicked} />
            </div>
          )}
          <Label className="flex items-center justify-between gap-4 rounded-input border border-border px-3 py-2.5 font-normal">
            <span className="grid gap-0.5">
              <span className="font-medium text-foreground">{t("subscriptions.new.trial")}</span>
              <span className="text-xs text-muted-foreground">
                {t("subscriptions.new.trialHelp")}
              </span>
            </span>
            <Switch
              checked={trial}
              onCheckedChange={(value) => {
                setTrial(value);
                setPlanId("");
              }}
            />
          </Label>
          <div className="grid gap-1.5">
            <Label htmlFor={ids.plan}>{t("subscriptions.fields.plan")}</Label>
            <PlanSelect id={ids.plan} value={planId} onChange={setPlanId} trial={trial} />
          </div>
          {trial ? null : (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="grid gap-1.5">
                  <Label htmlFor={ids.start}>{t("subscriptions.fields.starts")}</Label>
                  <Input
                    id={ids.start}
                    type="date"
                    value={start}
                    onChange={(event) => {
                      setStart(event.target.value);
                    }}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor={ids.end}>{t("subscriptions.fields.ends")}</Label>
                  <Input
                    id={ids.end}
                    type="date"
                    value={end}
                    min={start}
                    aria-describedby={`${ids.end}-help`}
                    onChange={(event) => {
                      setEnd(event.target.value);
                    }}
                  />
                  <p id={`${ids.end}-help`} className="text-xs text-muted-foreground">
                    {computedEnd
                      ? t("subscriptions.new.computedEnd", {
                          date: format.date(`${computedEnd}T12:00:00Z`),
                        })
                      : t("subscriptions.new.endHelp")}
                  </p>
                </div>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor={ids.note}>{t("subscriptions.fields.note")}</Label>
                <Textarea
                  id={ids.note}
                  dir="auto"
                  maxLength={200}
                  value={note}
                  onChange={(event) => {
                    setNote(event.target.value);
                  }}
                />
              </div>
            </>
          )}
          {error ? <p className="text-xs font-medium text-danger-text">{error}</p> : null}
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
            <Button type="submit" disabled={target === null || planId === ""} pending={pending}>
              {trial ? t("subscriptions.new.startTrial") : t("subscriptions.new.submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
