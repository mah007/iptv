import { ChevronDown } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { RelativeTime } from "./relative-time";

const TONE = {
  neutral: "text-muted-foreground",
  primary: "text-primary",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  info: "text-info",
  violet: "text-violet",
} as const;

export type TimelineTone = keyof typeof TONE;

/** Vertical list of events, newest first by convention (audit, subscription history, payments). */
export function Timeline({ className, ...props }: ComponentProps<"ol">) {
  return (
    <ol role="list" data-slot="timeline" className={cn("flex flex-col", className)} {...props} />
  );
}

export interface TimelineItemProps extends Omit<ComponentProps<"li">, "title"> {
  title: ReactNode;
  /** Defaults to a small dot. */
  icon?: ReactNode;
  tone?: TimelineTone;
  /** Who did it; `null` means the system did. */
  actor?: ReactNode;
  /** When it happened; shown relative, with the exact time in a tooltip. */
  at?: Date | string | number | undefined;
  /** Extra detail such as a DiffViewer, collapsed behind a toggle. */
  details?: ReactNode;
  /** Short description under the title. */
  children?: ReactNode;
}

export function TimelineItem({
  title,
  icon,
  tone = "neutral",
  actor,
  at,
  details,
  children,
  className,
  ...props
}: TimelineItemProps) {
  const { t } = useTranslation("ui");
  const actorNode = actor === null ? t("timeline.system") : actor;
  const hasMeta = actorNode !== undefined || at !== undefined;
  return (
    <li data-slot="timeline-item" className={cn("group/item flex gap-3", className)} {...props}>
      <div aria-hidden="true" className="flex flex-col items-center">
        <span
          className={cn(
            "grid size-7 shrink-0 place-items-center rounded-full border border-border bg-card [&_svg]:size-3.5",
            TONE[tone],
          )}
        >
          {icon ?? <span className="size-1.5 rounded-full bg-current" />}
        </span>
        <span className="w-px flex-1 bg-border group-last/item:hidden" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5 pb-5 pt-1 group-last/item:pb-0">
        <div className="text-ui font-medium text-foreground">{title}</div>
        {hasMeta ? (
          <div className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
            {actorNode !== undefined ? <span className="min-w-0 truncate">{actorNode}</span> : null}
            {actorNode !== undefined && at !== undefined ? (
              <span aria-hidden="true" className="size-0.5 rounded-full bg-current" />
            ) : null}
            {at !== undefined ? <RelativeTime value={at} /> : null}
          </div>
        ) : null}
        {children ? <div className="text-ui text-muted-foreground">{children}</div> : null}
        {details ? (
          <details className="group/details mt-1">
            <summary className="inline-flex cursor-pointer list-none select-none items-center gap-1 rounded-badge text-xs font-medium text-primary outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
              <ChevronDown
                aria-hidden="true"
                className="size-3.5 transition-transform duration-150 group-open/details:rotate-180"
              />
              <span className="group-open/details:hidden">{t("timeline.showDetails")}</span>
              <span className="hidden group-open/details:inline">{t("timeline.hideDetails")}</span>
            </summary>
            <div className="mt-2">{details}</div>
          </details>
        ) : null}
      </div>
    </li>
  );
}
