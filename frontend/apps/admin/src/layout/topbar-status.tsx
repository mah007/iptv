import { useDashboardKpis } from "@smart-iptv/api";
import {
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  LiveIndicator,
  useFormatters,
} from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { Bell, Cpu, ListChecks } from "lucide-react";
import { useTranslation } from "react-i18next";

import { LIBRARY_VIEW, useCan } from "../lib/auth";

/** How often the topbar's figures refresh. */
const REFRESH_MS = 15_000;

function useTopbarKpis() {
  const can = useCan();
  return useDashboardKpis({
    query: { enabled: can("dashboard.view"), refetchInterval: REFRESH_MS },
  });
}

/** Streams playing now; pulses when the count changes (SPEC §8.2 topbar). */
export function LiveStreams() {
  const { t } = useTranslation();
  const can = useCan();
  const kpis = useTopbarKpis();
  if (!can("customers.view") || kpis.data === undefined) return null;
  return (
    <Button asChild variant="ghost" size="sm" className="px-2">
      <Link to="/sessions" aria-label={t("topbar.live", { count: kpis.data.streams_now })}>
        <LiveIndicator count={kpis.data.streams_now} />
      </Link>
    </Button>
  );
}

/** Things that need an admin (SPEC §8.2 notifications bell): reviews, failed jobs. */
export function AlertsMenu() {
  const { t } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const kpis = useTopbarKpis();
  const data = kpis.data;
  if (data === undefined || !can(LIBRARY_VIEW)) return null;
  const reviews = data.reviews_open;
  const failed = data.transcode_failed_24h;
  const total = (reviews > 0 ? 1 : 0) + (failed > 0 ? 1 : 0);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          className="relative"
          aria-label={t("topbar.alerts.label", { count: total })}
        >
          <Bell aria-hidden="true" />
          {total > 0 ? (
            <span
              aria-hidden="true"
              className="absolute end-1 top-1 size-2 rounded-full bg-danger ring-2 ring-background"
            />
          ) : null}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuLabel>{t("topbar.alerts.title")}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {total === 0 ? (
          <p className="px-2 py-3 text-ui text-muted-foreground">{t("topbar.alerts.none")}</p>
        ) : null}
        {reviews > 0 ? (
          <DropdownMenuItem asChild>
            <Link to="/review">
              <ListChecks aria-hidden="true" />
              {t("topbar.alerts.reviews", { count: reviews, formatted: format.number(reviews) })}
            </Link>
          </DropdownMenuItem>
        ) : null}
        {failed > 0 ? (
          <DropdownMenuItem asChild>
            <Link to="/transcode" search={{ status: "failed" }}>
              <Cpu aria-hidden="true" />
              {t("topbar.alerts.failedJobs", { count: failed, formatted: format.number(failed) })}
            </Link>
          </DropdownMenuItem>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
