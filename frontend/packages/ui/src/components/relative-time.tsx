import { formatDistance } from "date-fns";
import { ar } from "date-fns/locale/ar";
import { enUS } from "date-fns/locale/en-US";
import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { DEFAULT_TIME_ZONE, isoDuration, useFormatters } from "../lib/format";
import { useNow } from "../lib/use-now";
import { Tooltip, TooltipContent, TooltipTrigger } from "./tooltip";

type DateInput = Date | string | number;

function toDate(value: DateInput): Date | null {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** date-fns words for the UI language; both use Latin digits and the Gregorian calendar. */
function distanceLocale(language: string) {
  return language.startsWith("ar") ? ar : enUS;
}

export interface RelativeTimeProps extends Omit<ComponentProps<"time">, "dateTime" | "children"> {
  value: DateInput;
  /** Zone of the absolute time in the tooltip: the admin's, Riyadh by default. */
  timeZone?: string;
  /** "3 minutes ago" / "in 3 days" (default), or a bare "3 minutes". */
  addSuffix?: boolean;
  /** How often the wording refreshes, in ms. */
  refreshMs?: number;
}

/** "3 minutes ago", with the exact date and time (and its zone) in a tooltip. */
export function RelativeTime({
  value,
  timeZone = DEFAULT_TIME_ZONE,
  addSuffix = true,
  refreshMs = 30_000,
  className,
  ...props
}: RelativeTimeProps) {
  const { i18n } = useTranslation("ui");
  const format = useFormatters(timeZone);
  const now = useNow(refreshMs);
  const date = toDate(value);
  if (date === null) return null;

  // The shared clock can lag by up to `refreshMs`: an event that just
  // happened must read "less than a minute ago", not "in less than a minute".
  const at = date.getTime();
  const reference = at > now && at - now <= refreshMs ? at : now;
  const relative = formatDistance(date, reference, {
    addSuffix,
    locale: distanceLocale(i18n.language),
  });
  const absolute = new Intl.DateTimeFormat(format.locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
    timeZoneName: "short",
  }).format(date);

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <time
          data-slot="relative-time"
          dateTime={date.toISOString()}
          className={cn("whitespace-nowrap", className)}
          {...props}
        >
          {relative}
        </time>
      </TooltipTrigger>
      <TooltipContent>{absolute}</TooltipContent>
    </Tooltip>
  );
}

export interface LiveDurationProps extends Omit<ComponentProps<"time">, "dateTime" | "children"> {
  /** When it started, e.g. a playback session's `started_at`. */
  since: DateInput;
}

/** Elapsed time that ticks every second ("1:12:05"); pauses while the tab is hidden. */
export function LiveDuration({ since, className, ...props }: LiveDurationProps) {
  const format = useFormatters();
  const now = useNow(1000);
  const start = toDate(since)?.getTime() ?? now;
  const seconds = Math.max(0, (now - start) / 1000);
  return (
    <time
      data-slot="live-duration"
      dateTime={isoDuration(seconds)}
      className={cn("whitespace-nowrap tabular-nums", className)}
      {...props}
    >
      {format.duration(seconds)}
    </time>
  );
}
