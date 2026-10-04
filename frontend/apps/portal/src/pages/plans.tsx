import {
  getMeInvoicesQueryKey,
  getMeSubscriptionQueryKey,
  isApiError,
  useBillingCheckout,
  useBillingPlans,
  useBillingProviders,
  type CheckoutResponse,
  type PaymentProviderCode,
  type PublicPlan,
} from "@smart-iptv/api-portal";
import {
  Alert,
  Badge,
  Button,
  CopyField,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  ErrorState,
  Skeleton,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Check, CreditCard, Landmark, Sparkles } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { localDescription, localName } from "../lib/local-name";
import { usePageTitle } from "../lib/page-title";

/** "1 month", "30 days", "1 month and 15 days": a plan's period in the UI language. */
export function usePeriod(): (
  plan: Pick<PublicPlan, "duration_months" | "duration_days">,
) => string {
  const { t } = useTranslation();
  return (plan) => {
    const parts = [
      plan.duration_months > 0 ? t("plans.months", { count: plan.duration_months }) : null,
      plan.duration_days > 0 ? t("plans.days", { count: plan.duration_days }) : null,
    ].filter((part): part is string => part !== null);
    return parts.join(t("plans.and"));
  };
}

/** Where a payment provider sends the customer back (ADR-0012: a portal page). */
function returnUrl(): string {
  return `${window.location.origin}/account/subscription?checkout=done`;
}

function PlanCard({ plan, onChoose }: { plan: PublicPlan; onChoose: () => void }) {
  const { t, i18n } = useTranslation();
  const period = usePeriod();
  const price = i18n.language === "ar" ? plan.price.display_ar : plan.price.display_en;
  const kinds = [
    plan.allow_movies ? t("plans.movies") : null,
    plan.allow_series ? t("plans.series") : null,
    plan.allow_live ? t("plans.live") : null,
  ].filter((kind): kind is string => kind !== null);
  const features = [
    t("plans.streams", { count: plan.max_streams }),
    t("plans.devices", { count: plan.max_devices }),
    t("plans.quality", {
      quality: plan.max_quality >= 2160 ? "4K" : `${String(plan.max_quality)}p`,
    }),
    ...(kinds.length > 0
      ? [new Intl.ListFormat(i18n.language, { type: "conjunction" }).format(kinds)]
      : []),
  ];
  const description = localDescription(plan, i18n.language);
  return (
    <li className="flex flex-col gap-4 rounded-card border border-border bg-card p-5 shadow-xs">
      <div className="flex items-start justify-between gap-2">
        <h2 className="text-lg font-semibold text-foreground">{localName(plan, i18n.language)}</h2>
        {plan.is_trial ? (
          <Badge tone="violet">
            <Sparkles aria-hidden="true" className="size-3" />
            {t("plans.trial")}
          </Badge>
        ) : null}
      </div>
      <p className="grid gap-0.5">
        <span className="text-3xl font-bold tabular-nums text-foreground" dir="auto">
          {plan.is_trial && plan.price.total === 0 ? t("plans.free") : price}
        </span>
        <span className="text-sm text-muted-foreground">
          {t("plans.per", { period: period(plan) })}
        </span>
      </p>
      {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
      <ul className="grid flex-1 gap-2 text-sm">
        {features.map((feature) => (
          <li key={feature} className="flex items-center gap-2 text-foreground">
            <Check aria-hidden="true" className="size-4 shrink-0 text-primary" />
            {feature}
          </li>
        ))}
      </ul>
      <Button onClick={onChoose} className="w-full">
        {plan.is_trial ? t("plans.startTrial") : t("plans.choose")}
      </Button>
    </li>
  );
}

function checkoutErrorKey(error: unknown): string {
  if (!isApiError(error)) return "plans.errors.UNEXPECTED";
  switch (error.code) {
    case "TRIAL_NOT_ELIGIBLE":
    case "PAYMENT_PROVIDER_UNAVAILABLE":
    case "PAYMENT_PROVIDER_ERROR":
    case "RATE_LIMITED":
    case "NETWORK_ERROR":
      return `plans.errors.${error.code}`;
    default:
      return "plans.errors.UNEXPECTED";
  }
}

function CheckoutDialog({
  plan,
  onOpenChange,
}: {
  plan: PublicPlan | null;
  onOpenChange: (open: boolean) => void;
}) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const queryClient = useQueryClient();
  const providers = useBillingProviders({ query: { enabled: plan !== null } });
  const checkout = useBillingCheckout();
  const [result, setResult] = useState<CheckoutResponse | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  function close(next: boolean): void {
    onOpenChange(next);
    if (!next) {
      setResult(null);
      setFailure(null);
    }
  }

  function pay(provider: PaymentProviderCode | undefined): void {
    if (plan === null) return;
    setFailure(null);
    checkout.mutate(
      {
        data: {
          plan_id: plan.id,
          ...(provider ? { provider } : {}),
          ...(provider && provider !== "manual" ? { return_url: returnUrl() } : {}),
        },
      },
      {
        onSuccess: (response) => {
          void queryClient.invalidateQueries({ queryKey: getMeSubscriptionQueryKey() });
          void queryClient.invalidateQueries({ queryKey: getMeInvoicesQueryKey() });
          if (response.kind === "redirect" && response.redirect_url) {
            window.location.assign(response.redirect_url);
            return;
          }
          setResult(response);
        },
        onError: (error) => {
          setFailure(checkoutErrorKey(error));
        },
      },
    );
  }

  const list = providers.data ?? [];
  const instructions = result?.instructions[i18n.language] ?? result?.instructions.en ?? "";

  return (
    <Dialog open={plan !== null} onOpenChange={close}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {result?.kind === "manual"
              ? t("plans.manualTitle")
              : result?.kind === "trial"
                ? t("plans.trialTitle")
                : t("plans.checkoutTitle", { plan: plan ? localName(plan, i18n.language) : "" })}
          </DialogTitle>
          <DialogDescription>
            {result?.kind === "manual"
              ? t("plans.manualDescription")
              : result?.kind === "trial"
                ? result.subscription?.status === "pending"
                  ? t("plans.trialPending")
                  : t("plans.trialStarted")
                : t("plans.checkoutDescription")}
          </DialogDescription>
        </DialogHeader>
        {failure ? <Alert tone="danger">{t(failure)}</Alert> : null}
        {result?.kind === "manual" ? (
          <div className="grid gap-4">
            {result.invoice ? (
              <p className="text-sm text-foreground">
                {t("plans.amountDue", {
                  amount: format.money(result.invoice.total, result.invoice.currency),
                })}
              </p>
            ) : null}

            {instructions ? (
              <div
                className="whitespace-pre-line rounded-card border border-border bg-muted/40 p-4 text-sm text-foreground"
                dir="auto"
              >
                {instructions}
              </div>
            ) : null}
            {result.reference ? (
              <CopyField label={t("plans.reference")} value={result.reference} />
            ) : null}
            <DialogFooter>
              <Button asChild variant="secondary">
                <Link to="/account/subscription">{t("plans.toSubscription")}</Link>
              </Button>
              <Button
                onClick={() => {
                  close(false);
                }}
              >
                {t("plans.done")}
              </Button>
            </DialogFooter>
          </div>
        ) : result?.kind === "trial" ? (
          <DialogFooter>
            <Button asChild>
              <Link to="/">{t("plans.startWatching")}</Link>
            </Button>
          </DialogFooter>
        ) : plan?.is_trial ? (
          <DialogFooter>
            <Button
              pending={checkout.isPending}
              onClick={() => {
                pay(undefined);
              }}
            >
              {t("plans.startTrial")}
            </Button>
          </DialogFooter>
        ) : providers.isPending ? (
          <Skeleton className="h-20 w-full" />
        ) : list.length === 0 ? (
          <Alert tone="warning">{t("plans.noProviders")}</Alert>
        ) : (
          <div className="grid gap-2">
            {list.map((provider) => (
              <Button
                key={provider.code}
                size="lg"
                variant={provider.code === "manual" ? "secondary" : "primary"}
                className="justify-start"
                pending={checkout.isPending && checkout.variables.data.provider === provider.code}
                disabled={checkout.isPending}
                onClick={() => {
                  pay(provider.code);
                }}
              >
                {provider.code === "manual" ? (
                  <Landmark aria-hidden="true" />
                ) : (
                  <CreditCard aria-hidden="true" />
                )}
                {provider.code === "manual"
                  ? t("plans.payManual")
                  : t("plans.payWith", { provider: provider.name })}
              </Button>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Plans and checkout (SPEC §9, ADR-0012): manual payment instructions; providers only when enabled. */
export function PlansPage() {
  const { t } = useTranslation();
  usePageTitle(t("plans.title"));
  const plans = useBillingPlans();
  const [chosen, setChosen] = useState<PublicPlan | null>(null);

  return (
    <div className="page-top mx-auto grid max-w-6xl gap-6 px-4 sm:px-6">
      <div className="grid gap-1">
        <h1 className="text-2xl font-semibold text-foreground sm:text-3xl">{t("plans.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("plans.subtitle")}</p>
      </div>
      {plans.isError ? (
        <ErrorState
          error={plans.error}
          onRetry={() => {
            void plans.refetch();
          }}
        />
      ) : plans.isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton key={index} className="h-80 w-full" />
          ))}
        </div>
      ) : plans.data.length === 0 ? (
        <EmptyState
          icon={<CreditCard />}
          title={t("plans.emptyTitle")}
          description={t("plans.empty")}
        />
      ) : (
        <ul role="list" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {plans.data.map((plan) => (
            <PlanCard
              key={plan.id}
              plan={plan}
              onChoose={() => {
                setChosen(plan);
              }}
            />
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">{t("plans.vatNote")}</p>
      <CheckoutDialog
        plan={chosen}
        onOpenChange={(open) => {
          if (!open) setChosen(null);
        }}
      />
    </div>
  );
}
