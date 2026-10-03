import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { useId, useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Area, AreaChart, XAxis, YAxis } from "recharts";

import { cn } from "../lib/cn";
import { useFormatters } from "../lib/format";
import { Skeleton } from "./skeleton";

export interface StatTileProps {
  label: ReactNode;
  /** Already formatted for display (e.g. "1,284" or "29.00 SAR"). */
  value: ReactNode;
  /**
   * Change against the previous period. `percent` is a ratio (0.125 = +12.5%);
   * `number` is an absolute difference.
   */
  delta?: number | undefined;
  deltaFormat?: "percent" | "number";
  /** E.g. "vs previous 30 days". */
  deltaLabel?: ReactNode;
  /** A decrease is good news (e.g. churn, failed jobs). */
  invertDelta?: boolean;
  /** Recent values, oldest first, drawn as a sparkline. */
  sparkline?: readonly number[] | undefined;
  icon?: ReactNode;
  loading?: boolean;
  className?: string;
}

type Trend = "up" | "down" | "flat";

function trendOf(delta: number): Trend {
  if (delta > 0) return "up";
  if (delta < 0) return "down";
  return "flat";
}

const TREND_ICON = { up: ArrowUpRight, down: ArrowDownRight, flat: Minus } as const;

function Sparkline({ values }: { values: readonly number[] }) {
  const { i18n } = useTranslation("ui");
  const gradientId = useId();
  const data = useMemo(() => values.map((value, index) => ({ index, value })), [values]);
  return (
    <div aria-hidden="true" className="h-9 w-full">
      <AreaChart
        data={data}
        responsive
        width="100%"
        height="100%"
        margin={{ top: 2, right: 0, bottom: 0, left: 0 }}
        accessibilityLayer={false}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--primary)" stopOpacity={0.22} />
            <stop offset="100%" stopColor="var(--primary)" stopOpacity={0} />
          </linearGradient>
        </defs>
        {/* Time runs from the inline start: right-to-left in Arabic. */}
        <XAxis dataKey="index" hide reversed={i18n.dir(i18n.language) === "rtl"} />
        <YAxis hide domain={["dataMin", "dataMax"]} />
        <Area
          type="monotone"
          dataKey="value"
          stroke="var(--primary)"
          strokeWidth={1.5}
          fill={`url(#${gradientId})`}
          isAnimationActive={false}
          dot={false}
          activeDot={false}
        />
      </AreaChart>
    </div>
  );
}

/** KPI tile: label, value, change against the previous period, optional sparkline. */
export function StatTile({
  label,
  value,
  delta,
  deltaFormat = "percent",
  deltaLabel,
  invertDelta = false,
  sparkline,
  icon,
  loading = false,
  className,
}: StatTileProps) {
  const { t } = useTranslation("ui");
  const format = useFormatters();

  let deltaNode: ReactNode = null;
  if (delta !== undefined && !loading) {
    const trend = trendOf(delta);
    const good = trend === "flat" ? null : (trend === "up") !== invertDelta;
    const magnitude =
      deltaFormat === "percent" ? format.percent(Math.abs(delta)) : format.number(Math.abs(delta));
    // Intl places the sign correctly for each locale (and adds bidi marks for Arabic).
    const signed =
      deltaFormat === "percent"
        ? format.percent(delta, { signDisplay: "exceptZero" })
        : format.number(delta, { signDisplay: "exceptZero" });
    const Icon = TREND_ICON[trend];
    deltaNode = (
      <span
        data-trend={trend}
        className={cn(
          "inline-flex items-center gap-0.5 rounded-badge px-1 text-xs font-medium",
          good === true && "bg-success/10 text-success-text",
          good === false && "bg-danger/10 text-danger-text",
          good === null && "bg-muted text-muted-foreground",
        )}
      >
        <Icon aria-hidden="true" className="size-3.5 rtl:-scale-x-100" />
        <bdi aria-hidden="true">{signed}</bdi>
        <span className="sr-only">{t(`statTile.${trend}`, { value: magnitude })}</span>
      </span>
    );
  }

  return (
    <section
      data-slot="stat-tile"
      aria-busy={loading || undefined}
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-card border border-border bg-card p-(--density-card) shadow-xs",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="truncate text-ui font-medium text-muted-foreground">{label}</h3>
        {icon ? (
          <span aria-hidden="true" className="text-muted-foreground [&_svg]:size-4">
            {icon}
          </span>
        ) : null}
      </div>
      {loading ? (
        <>
          <Skeleton className="h-8 w-24" />
          <Skeleton className="h-4 w-32" />
        </>
      ) : (
        <>
          <div className="text-2xl font-semibold leading-8 text-foreground tabular-nums ltr:tracking-tight">
            {value}
          </div>
          {deltaNode || deltaLabel ? (
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              {deltaNode}
              {deltaLabel ? <span>{deltaLabel}</span> : null}
            </div>
          ) : null}
        </>
      )}
      {sparkline && sparkline.length > 1 ? (
        loading ? (
          <Skeleton className="h-9 w-full" />
        ) : (
          <Sparkline values={sparkline} />
        )
      ) : null}
    </section>
  );
}
