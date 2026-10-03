import { Progress as ProgressPrimitive } from "radix-ui";
import { useId, type ComponentProps, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { useFormatters } from "../lib/format";

const TONE = {
  primary: "bg-primary",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
} as const;

export interface ProgressBarProps extends Omit<
  ComponentProps<typeof ProgressPrimitive.Root>,
  "value" | "max" | "children"
> {
  /** Work done, from 0 to `max`. Leave out (or null) for an indeterminate bar. */
  value?: number | null | undefined;
  max?: number;
  /** Visible label; it also names the progress bar (otherwise pass `aria-label`). */
  label?: ReactNode;
  /** Remaining time in seconds, shown as "3 min left". */
  etaSeconds?: number | null | undefined;
  /** Speed as a multiple of real time, shown as "1.8×". */
  speed?: number | null | undefined;
  /** Hide the percentage. */
  hideValue?: boolean;
  tone?: keyof typeof TONE;
  /** `inline` is a slim single line for table cells. */
  variant?: "default" | "inline";
}

/** Determinate or indeterminate progress with percentage, ETA and speed (SPEC §8.3 page 10). */
export function ProgressBar({
  value,
  max = 100,
  label,
  etaSeconds,
  speed,
  hideValue = false,
  tone = "primary",
  variant = "default",
  className,
  ...props
}: ProgressBarProps) {
  const { t } = useTranslation("ui");
  const format = useFormatters();
  const labelId = useId();

  const determinate = typeof value === "number" && Number.isFinite(value) && max > 0;
  // Radix rejects out-of-range values (and falls back to indeterminate), so clamp first.
  const clamped = determinate ? Math.min(Math.max(value, 0), max) : null;
  const ratio = clamped === null ? null : clamped / max;
  const percent = ratio === null ? null : format.percent(ratio, { maximumFractionDigits: 0 });
  const eta =
    typeof etaSeconds === "number" && Number.isFinite(etaSeconds) && etaSeconds >= 0
      ? t("progress.eta", { duration: format.duration(etaSeconds, "short") })
      : null;
  const pace =
    typeof speed === "number" && Number.isFinite(speed) && speed > 0
      ? t("progress.speed", { value: format.number(speed, { maximumFractionDigits: 1 }) })
      : null;
  const valueText = [percent, eta].filter(Boolean).join(t("separator"));
  const inline = variant === "inline";

  const bar = (
    <ProgressPrimitive.Root
      data-slot="progress-bar"
      value={clamped}
      max={max}
      getValueLabel={() => valueText}
      aria-labelledby={label ? labelId : undefined}
      className={cn(
        "relative w-full overflow-hidden rounded-full bg-muted",
        inline ? "h-1.5" : "h-2",
      )}
      {...props}
    >
      <ProgressPrimitive.Indicator
        className={cn(
          "h-full rounded-full",
          TONE[tone],
          ratio === null
            ? "w-1/3 animate-indeterminate"
            : "transition-[width] duration-200 ease-out",
        )}
        // Width, not a transform, so the bar fills from the inline start in RTL too.
        style={ratio === null ? undefined : { width: `${String(ratio * 100)}%` }}
      />
    </ProgressPrimitive.Root>
  );

  if (inline) {
    return (
      <div data-slot="progress" className={cn("flex min-w-0 items-center gap-2", className)}>
        {label ? (
          <span id={labelId} className="sr-only">
            {label}
          </span>
        ) : null}
        <div className="min-w-12 flex-1">{bar}</div>
        {!hideValue && percent ? (
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{percent}</span>
        ) : null}
        {eta ? (
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{eta}</span>
        ) : null}
      </div>
    );
  }

  return (
    <div data-slot="progress" className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      {label || (!hideValue && percent) ? (
        <div className="flex items-baseline justify-between gap-3 text-ui">
          {label ? (
            <span id={labelId} className="min-w-0 truncate font-medium text-foreground">
              {label}
            </span>
          ) : (
            <span />
          )}
          {!hideValue && percent ? (
            <span className="shrink-0 tabular-nums text-muted-foreground">{percent}</span>
          ) : null}
        </div>
      ) : null}
      {bar}
      {pace || eta ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs tabular-nums text-muted-foreground">
          {pace ? <span data-slot="progress-speed">{pace}</span> : null}
          {eta ? <span data-slot="progress-eta">{eta}</span> : null}
        </div>
      ) : null}
    </div>
  );
}
