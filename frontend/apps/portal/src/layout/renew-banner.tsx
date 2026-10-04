import { useMeSubscription } from "@smart-iptv/api-portal";
import { Button } from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import { accessState, type AccessState } from "../lib/access";
import { useMe } from "../lib/auth";
import { useCustomerFormatters } from "../lib/formatters";

/** The customer's access, from the cached profile and subscription. */
export function useAccessState(): AccessState {
  const me = useMe();
  const subscription = useMeSubscription({ query: { staleTime: 5 * 60_000 } });
  return accessState(me, subscription.data);
}

export function needsRenewNotice(state: AccessState): boolean {
  return state.kind === "expired" || state.kind === "grace" || state.kind === "suspended";
}

/** A strip under the top bar when the subscription ended or is in its grace days. */
export function RenewBanner({ state }: { state: AccessState }) {
  const { t } = useTranslation();
  const format = useCustomerFormatters();
  if (!needsRenewNotice(state)) return null;
  const message =
    state.kind === "grace"
      ? state.graceUntil
        ? t("renew.grace", { date: format.date(state.graceUntil) })
        : t("renew.graceNoDate")
      : state.kind === "suspended"
        ? t("renew.suspended")
        : t("renew.expired");
  return (
    <div role="status" className="border-b border-warning/30 bg-warning/10 pt-16 text-warning-text">
      <div className="mx-auto flex max-w-[1800px] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5 text-sm sm:px-6 lg:px-10">
        <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />
        <span className="min-w-0 flex-1">{message}</span>
        {state.kind === "suspended" ? null : (
          <Button asChild size="sm">
            <Link to="/plans">{t("renew.cta")}</Link>
          </Button>
        )}
      </div>
    </div>
  );
}
