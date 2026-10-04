import {
  ManualPaymentMethodEnum,
  getDashboardBillingQueryKey,
  getInvoicesListQueryKey,
  getPaymentsListQueryKey,
  getPaymentsRetrieveQueryKey,
  getSubscriptionsListQueryKey,
  usePaymentsRecordManual,
  usePaymentsRefund,
  usePlansList,
  type PaymentDetail,
  type ManualPaymentMethodEnum as ManualMethod,
} from "@smart-iptv/api";
import {
  Button,
  Checkbox,
  DEFAULT_TIME_ZONE,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { useMe } from "../../lib/auth";
import { notifyError, translatedFieldErrors } from "../../lib/problems";
import { endOfDayIn, isoDateIn } from "../../lib/time";
import { majorToMinor, minorToMajor } from "./money";
import { CustomerPicker, PlanSelect, type PickedCustomer } from "./pickers";

function usePaymentRefresh() {
  const queryClient = useQueryClient();
  return async () => {
    await Promise.all(
      [
        getPaymentsListQueryKey(),
        getInvoicesListQueryKey(),
        getSubscriptionsListQueryKey(),
        getDashboardBillingQueryKey(),
      ].map((queryKey) => queryClient.invalidateQueries({ queryKey })),
    );
  };
}

/** A fresh key per opened dialog: a double submit records one payment (the API dedupes). */
function newIdempotencyKey(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

/**
 * Record a bank transfer or cash payment (SPEC §7.6 manual provider). With a
 * plan, the payment activates or renews it and issues an invoice.
 */
export function RecordPaymentDialog({
  open,
  onOpenChange,
  customer,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  customer?: PickedCustomer | undefined;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const me = useMe();
  const timeZone = me?.timezone ?? DEFAULT_TIME_ZONE;
  const ids = {
    customer: useId(),
    plan: useId(),
    amount: useId(),
    method: useId(),
    reference: useId(),
    paid: useId(),
  };
  const refresh = usePaymentRefresh();
  const record = usePaymentsRecordManual();
  const plans = usePlansList({ active: true, is_trial: false });
  const [picked, setPicked] = useState<PickedCustomer | null>(customer ?? null);
  const [planId, setPlanId] = useState("");
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<ManualMethod>("bank_transfer");
  const [reference, setReference] = useState("");
  const [paidOn, setPaidOn] = useState("");
  const [key, setKey] = useState(newIdempotencyKey);
  const [error, setError] = useState<string | null>(null);
  const plan = plans.data?.find((candidate) => candidate.id === planId);
  const currency = plan?.currency ?? "SAR";
  const minor = amount.trim() === "" ? null : majorToMinor(amount, currency);
  const amountInvalid = amount.trim() !== "" && minor === null;
  const target = customer ?? picked;
  const today = isoDateIn(new Date(), timeZone);

  function reset(): void {
    setPicked(customer ?? null);
    setPlanId("");
    setAmount("");
    setMethod("bank_transfer");
    setReference("");
    setPaidOn("");
    setKey(newIdempotencyKey());
    setError(null);
  }

  async function submit(): Promise<void> {
    if (target === null || amountInvalid || (planId === "" && minor === null)) return;
    setError(null);
    try {
      await record.mutateAsync({
        data: {
          user_id: target.id,
          plan_id: planId || null,
          amount: minor,
          method,
          reference: reference.trim(),
          paid_at: paidOn && paidOn !== today ? endOfDayIn(paidOn, timeZone) : null,
          idempotency_key: key,
        },
      });
      await refresh();
      toast.success(t("payments.record.done", { name: target.name }));
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
          <DialogTitle>{t("payments.record.title")}</DialogTitle>
          <DialogDescription>{t("payments.record.description")}</DialogDescription>
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
              <Label htmlFor={ids.customer}>{t("payments.fields.customer")}</Label>
              <CustomerPicker id={ids.customer} value={picked} onChange={setPicked} />
            </div>
          )}
          <div className="grid gap-1.5">
            <Label htmlFor={ids.plan}>{t("payments.fields.plan")}</Label>
            <PlanSelect id={ids.plan} value={planId} onChange={setPlanId} />
            <p className="text-xs text-muted-foreground">{t("payments.record.planHelp")}</p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor={ids.amount}>{t("payments.fields.amount", { currency })}</Label>
              <Input
                id={ids.amount}
                inputMode="decimal"
                dir="ltr"
                className="tabular-nums"
                placeholder={plan ? minorToMajor(plan.price_total.total, currency) : ""}
                value={amount}
                aria-invalid={amountInvalid ? true : undefined}
                aria-describedby={`${ids.amount}-help`}
                onChange={(event) => {
                  setAmount(event.target.value);
                }}
              />
              <p id={`${ids.amount}-help`} className="text-xs text-muted-foreground">
                {amountInvalid
                  ? t("payments.validation.amount")
                  : plan
                    ? t("payments.record.amountHelp", {
                        price: format.money(plan.price_total.total, currency),
                      })
                    : t("payments.record.amountRequired")}
              </p>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={ids.method}>{t("payments.fields.method")}</Label>
              <Select
                value={method}
                onValueChange={(value) => {
                  setMethod(value as ManualMethod);
                }}
              >
                <SelectTrigger id={ids.method}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.values(ManualPaymentMethodEnum).map((value) => (
                    <SelectItem key={value} value={value}>
                      {t(`payments.methods.${value}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor={ids.reference}>{t("payments.fields.reference")}</Label>
              <Input
                id={ids.reference}
                dir="auto"
                maxLength={100}
                value={reference}
                onChange={(event) => {
                  setReference(event.target.value);
                }}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={ids.paid}>{t("payments.fields.paidOn")}</Label>
              <Input
                id={ids.paid}
                type="date"
                max={today}
                value={paidOn}
                onChange={(event) => {
                  setPaidOn(event.target.value);
                }}
              />
            </div>
          </div>
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
            <Button
              type="submit"
              disabled={target === null || amountInvalid || (planId === "" && minor === null)}
              pending={record.isPending}
            >
              {t("payments.record.submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Refund all or part of a payment (SPEC §8.3.13); optionally end its subscription. */
export function RefundDialog({
  payment,
  open,
  onOpenChange,
}: {
  payment: PaymentDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const ids = { amount: useId(), reason: useId(), cancel: useId() };
  const queryClient = useQueryClient();
  const refresh = usePaymentRefresh();
  const refund = usePaymentsRefund();
  const refundable = payment.amount - payment.refunded_amount;
  const [amount, setAmount] = useState(() => minorToMajor(refundable, payment.currency));
  const [reason, setReason] = useState("");
  const [cancel, setCancel] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const minor = majorToMinor(amount, payment.currency);
  const invalid = minor === null || minor < 1 || minor > refundable;

  async function submit(): Promise<void> {
    if (invalid) return;
    setError(null);
    try {
      await refund.mutateAsync({
        id: payment.id,
        data: {
          amount: minor === refundable ? null : minor,
          reason: reason.trim(),
          cancel_subscription: cancel,
        },
      });
      await Promise.all([
        refresh(),
        queryClient.invalidateQueries({ queryKey: getPaymentsRetrieveQueryKey(payment.id) }),
      ]);
      toast.success(t("payments.refund.done", { amount: format.money(minor, payment.currency) }));
      onOpenChange(false);
    } catch (failure) {
      const messages = translatedFieldErrors(t, failure);
      if (messages[0]) setError(messages[0]);
      else notifyError(t, failure);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent role="alertdialog">
        <DialogHeader>
          <DialogTitle>{t("payments.refund.title")}</DialogTitle>
          <DialogDescription>
            {t("payments.refund.description", {
              amount: format.money(refundable, payment.currency),
            })}
          </DialogDescription>
        </DialogHeader>
        <form
          noValidate
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <div className="grid gap-1.5">
            <Label htmlFor={ids.amount}>
              {t("payments.fields.amount", { currency: payment.currency })}
            </Label>
            <Input
              id={ids.amount}
              inputMode="decimal"
              dir="ltr"
              className="w-40 tabular-nums"
              value={amount}
              aria-invalid={invalid ? true : undefined}
              onChange={(event) => {
                setAmount(event.target.value);
              }}
            />
            {invalid ? (
              <p className="text-xs font-medium text-danger-text">
                {t("payments.validation.refund", {
                  max: format.money(refundable, payment.currency),
                })}
              </p>
            ) : null}
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={ids.reason}>{t("payments.fields.reason")}</Label>
            <Input
              id={ids.reason}
              dir="auto"
              maxLength={200}
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
              }}
            />
          </div>
          {payment.subscription_id ? (
            <Label htmlFor={ids.cancel} className="flex items-start gap-2.5 font-normal">
              <Checkbox
                id={ids.cancel}
                checked={cancel}
                onCheckedChange={(value) => {
                  setCancel(value === true);
                }}
                className="mt-0.5"
              />
              <span className="grid gap-0.5">
                <span className="text-foreground">{t("payments.refund.cancelSubscription")}</span>
                <span className="text-xs text-muted-foreground">
                  {t("payments.refund.cancelHelp")}
                </span>
              </span>
            </Label>
          ) : null}
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
            <Button type="submit" variant="danger" disabled={invalid} pending={refund.isPending}>
              {t("payments.refund.submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
