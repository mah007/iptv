import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { useFormatters } from "../lib/format";

export interface LiveIndicatorProps extends Omit<ComponentProps<"span">, "children"> {
  count: number;
  /** Spoken label; defaults to "{count} live streams". */
  label?: string;
}

/**
 * Pulsing dot and a count (e.g. active streams in the topbar). The number
 * pops when it changes; both animations stop under prefers-reduced-motion.
 */
export function LiveIndicator({ count, label, className, ...props }: LiveIndicatorProps) {
  const { t } = useTranslation("ui");
  const format = useFormatters();
  const live = count > 0;
  return (
    <span
      data-slot="live-indicator"
      data-live={live || undefined}
      className={cn(
        "inline-flex items-center gap-1.5 text-ui font-medium tabular-nums text-foreground",
        className,
      )}
      {...props}
    >
      <span aria-hidden="true" className="relative flex size-2">
        {live ? (
          <span className="absolute inline-flex size-full rounded-full bg-success opacity-60 motion-safe:animate-ping motion-safe:[animation-duration:2s]" />
        ) : null}
        <span
          className={cn(
            "relative inline-flex size-2 rounded-full",
            live ? "bg-success" : "bg-neutral",
          )}
        />
      </span>
      {/* A new key on change replays the pop-in. */}
      <span key={count} aria-hidden="true" className="motion-safe:animate-pop-in">
        {format.number(count)}
      </span>
      <span className="sr-only">{label ?? t("live.streams", { count })}</span>
    </span>
  );
}
