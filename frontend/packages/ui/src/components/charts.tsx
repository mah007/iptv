import { useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { cn } from "../lib/cn";
import { Skeleton } from "./skeleton";

/*
 * Chart wrappers (SPEC §8.1 Charts): one accent series and grey for the rest,
 * hairline grid, a tooltip with units, tabular numbers and axes that mirror in
 * Arabic (time runs from the inline start). Every chart also renders its data as
 * a visually hidden table, so screen readers get the numbers, not a picture.
 */

/** A series is the accent (the one the chart is about) or the grey de-emphasis tone. */
export type ChartTone = "accent" | "muted";

const TONE_COLOR: Record<ChartTone, string> = {
  accent: "var(--chart-accent)",
  muted: "var(--chart-muted)",
};

const TICK = { fill: "var(--muted-foreground)", fontSize: 12 } as const;

function useRtl(): boolean {
  const { i18n } = useTranslation("ui");
  return i18n.dir(i18n.language) === "rtl";
}

interface ChartFrameProps {
  /** Names the chart for assistive technology, e.g. "Concurrent streams, last 24 hours". */
  label: string;
  height: number;
  loading: boolean;
  /** Column headings and rows of the hidden data table. */
  table: { columns: readonly string[]; rows: readonly (readonly ReactNode[])[] };
  className?: string | undefined;
  children: ReactNode;
}

function ChartFrame({ label, height, loading, table, className, children }: ChartFrameProps) {
  if (loading) {
    return <Skeleton className={cn("w-full", className)} style={{ height }} />;
  }
  return (
    <figure data-slot="chart" className={cn("m-0 min-w-0", className)}>
      <div aria-hidden="true" className="w-full tabular-nums" style={{ height }}>
        {children}
      </div>
      <table className="sr-only">
        <caption>{label}</caption>
        <thead>
          <tr>
            {table.columns.map((column) => (
              <th key={column} scope="col">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, index) => (
            <tr key={index}>
              {row.map((cell, cellIndex) => (
                <td key={cellIndex}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

function TooltipCard({ title, rows }: { title: ReactNode; rows: readonly TooltipRow[] }) {
  return (
    <div className="grid min-w-36 gap-1 rounded-input border border-border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-elevation">
      <div className="font-medium text-foreground">{title}</div>
      {rows.map((row) => (
        <div key={row.key} className="flex items-center justify-between gap-4">
          <span className="flex items-center gap-1.5 text-muted-foreground">
            <span
              aria-hidden="true"
              className="size-2 rounded-full"
              style={{ background: TONE_COLOR[row.tone] }}
            />
            {row.label}
          </span>
          <span className="font-medium text-foreground tabular-nums">{row.value}</span>
        </div>
      ))}
    </div>
  );
}

interface TooltipRow {
  key: string;
  label: string;
  tone: ChartTone;
  value: string;
}

/** A legend for two or more series: the identity channel that doesn't rely on colour alone. */
export function ChartLegend({
  series,
  className,
}: {
  series: readonly { key: string; label: string; tone: ChartTone }[];
  className?: string;
}) {
  return (
    <ul className={cn("flex flex-wrap items-center gap-x-4 gap-y-1 text-xs", className)}>
      {series.map((item) => (
        <li key={item.key} className="flex items-center gap-1.5 text-muted-foreground">
          <span
            aria-hidden="true"
            className="h-2 w-3 rounded-[2px]"
            style={{ background: TONE_COLOR[item.tone] }}
          />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

// --- Time series (area) ----------------------------------------------------------------

export interface TimeSeriesPoint {
  /** ISO 8601 instant or date. */
  at: string;
  value: number;
}

export interface TimeSeriesChartProps {
  label: string;
  /** What the value is, for the tooltip and the table, e.g. "Streams". */
  valueLabel: string;
  data: readonly TimeSeriesPoint[];
  formatTick: (at: string) => string;
  formatTooltip: (at: string) => string;
  formatValue: (value: number) => string;
  /** A dashed horizontal reference, e.g. the peak or a capacity (label it beside the chart). */
  reference?: { value: number } | undefined;
  height?: number;
  loading?: boolean;
  className?: string;
}

/** One series over time as a thin line over a 10% wash of the accent. */
export function TimeSeriesChart({
  label,
  valueLabel,
  data,
  formatTick,
  formatTooltip,
  formatValue,
  reference,
  height = 220,
  loading = false,
  className,
}: TimeSeriesChartProps) {
  const rtl = useRtl();
  const gradient = useId();
  const rows = data.map((point) => [formatTooltip(point.at), formatValue(point.value)] as const);
  return (
    <ChartFrame
      label={label}
      height={height}
      loading={loading}
      className={className}
      table={{ columns: ["", valueLabel], rows }}
    >
      <AreaChart
        data={[...data]}
        responsive
        width="100%"
        height="100%"
        margin={{ top: 8, right: 4, bottom: 0, left: 4 }}
        accessibilityLayer={false}
      >
        <defs>
          <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-accent)" stopOpacity={0.18} />
            <stop offset="100%" stopColor="var(--chart-accent)" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeWidth={1} />
        <XAxis
          dataKey="at"
          reversed={rtl}
          tickFormatter={formatTick}
          tick={TICK}
          tickLine={false}
          axisLine={{ stroke: "var(--chart-grid)" }}
          minTickGap={24}
        />
        <YAxis
          orientation={rtl ? "right" : "left"}
          allowDecimals={false}
          tickFormatter={formatValue}
          tick={TICK}
          tickLine={false}
          axisLine={false}
          width={40}
        />
        {reference ? (
          // Unlabelled: SVG text can't mirror for Arabic; name the reference beside the chart.
          <ReferenceLine
            y={reference.value}
            stroke="var(--muted-foreground)"
            strokeDasharray="4 4"
            ifOverflow="extendDomain"
          />
        ) : null}
        <Tooltip
          cursor={{ stroke: "var(--muted-foreground)", strokeWidth: 1 }}
          content={(props) => {
            const point = props.payload[0];
            if (!props.active || point === undefined) return null;
            const at = String(props.label ?? "");
            return (
              <TooltipCard
                title={formatTooltip(at)}
                rows={[
                  {
                    key: "value",
                    label: valueLabel,
                    tone: "accent",
                    value: formatValue(Number(point.value ?? 0)),
                  },
                ]}
              />
            );
          }}
        />
        <Area
          type="monotone"
          dataKey="value"
          name={valueLabel}
          stroke="var(--chart-accent)"
          strokeWidth={2}
          fill={`url(#${gradient})`}
          isAnimationActive={false}
          dot={false}
          activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--card)" }}
        />
      </AreaChart>
    </ChartFrame>
  );
}

// --- Columns (grouped, per period) -------------------------------------------------------

export interface ColumnSeries {
  key: string;
  label: string;
  tone: ChartTone;
}

export interface ColumnChartProps {
  label: string;
  series: readonly ColumnSeries[];
  /** One row per period: `at` plus a number per series key. */
  data: readonly ({ at: string } & Record<string, number | string>)[];
  formatTick: (at: string) => string;
  formatTooltip: (at: string) => string;
  formatValue: (value: number) => string;
  height?: number;
  loading?: boolean;
  className?: string;
}

/** Thin grouped columns per period (≤ 24 px, rounded data end, square at the baseline). */
export function ColumnChart({
  label,
  series,
  data,
  formatTick,
  formatTooltip,
  formatValue,
  height = 220,
  loading = false,
  className,
}: ColumnChartProps) {
  const rtl = useRtl();
  const rows = data.map((row) => [
    formatTooltip(row.at),
    ...series.map((item) => formatValue(Number(row[item.key] ?? 0))),
  ]);
  return (
    <div className={cn("grid gap-3", className)}>
      {series.length > 1 && !loading ? <ChartLegend series={series} /> : null}
      <ChartFrame
        label={label}
        height={height}
        loading={loading}
        table={{ columns: ["", ...series.map((item) => item.label)], rows }}
      >
        <BarChart
          data={[...data]}
          responsive
          width="100%"
          height="100%"
          margin={{ top: 8, right: 4, bottom: 0, left: 4 }}
          barGap={2}
          barCategoryGap="20%"
          accessibilityLayer={false}
        >
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeWidth={1} />
          <XAxis
            dataKey="at"
            reversed={rtl}
            tickFormatter={formatTick}
            tick={TICK}
            tickLine={false}
            axisLine={{ stroke: "var(--chart-grid)" }}
            minTickGap={16}
          />
          <YAxis
            orientation={rtl ? "right" : "left"}
            allowDecimals={false}
            tickFormatter={formatValue}
            tick={TICK}
            tickLine={false}
            axisLine={false}
            width={40}
          />
          <Tooltip
            cursor={{ fill: "var(--muted)", opacity: 0.6 }}
            content={(props) => {
              if (!props.active || props.payload.length === 0) return null;
              const at = String(props.label ?? "");
              const row = data.find((item) => item.at === at);
              return (
                <TooltipCard
                  title={formatTooltip(at)}
                  rows={series.map((item) => ({
                    key: item.key,
                    label: item.label,
                    tone: item.tone,
                    value: formatValue(Number(row?.[item.key] ?? 0)),
                  }))}
                />
              );
            }}
          />
          {series.map((item) => (
            <Bar
              key={item.key}
              dataKey={item.key}
              name={item.label}
              fill={TONE_COLOR[item.tone]}
              maxBarSize={24}
              radius={[4, 4, 0, 0]}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ChartFrame>
    </div>
  );
}

// --- Ranked bars --------------------------------------------------------------------------

export interface BarListItem {
  key: string;
  label: ReactNode;
  value: number;
  /** Rendered around the label, e.g. a link to the filtered list. */
  wrap?: (label: ReactNode) => ReactNode;
}

export interface BarListProps {
  label: string;
  valueLabel: string;
  items: readonly BarListItem[];
  formatValue: (value: number) => string;
  loading?: boolean;
  className?: string;
}

/**
 * Horizontal bars for a ranking (top categories, top titles): HTML, so labels
 * wrap, mirror and stay text, with the bar growing from the inline start.
 */
export function BarList({
  label,
  valueLabel,
  items,
  formatValue,
  loading = false,
  className,
}: BarListProps) {
  if (loading) {
    return (
      <div className={cn("grid gap-3", className)} aria-busy="true">
        {[0, 1, 2, 3].map((row) => (
          <Skeleton key={row} className="h-6 w-full" />
        ))}
      </div>
    );
  }
  const max = Math.max(1, ...items.map((item) => item.value));
  return (
    <table
      data-slot="bar-list"
      className={cn("w-full border-separate border-spacing-y-1.5", className)}
    >
      <caption className="sr-only">{label}</caption>
      <thead className="sr-only">
        <tr>
          <th scope="col">{label}</th>
          <th scope="col">{valueLabel}</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.key} className="text-ui">
            <td className="relative w-full p-0">
              <span
                aria-hidden="true"
                className="absolute inset-y-0 start-0 rounded-e-[4px] bg-(--chart-accent)/15"
                style={{ width: `${String(Math.max(2, (item.value / max) * 100))}%` }}
              />
              <span className="relative block truncate px-2 py-1 text-foreground">
                {item.wrap ? item.wrap(item.label) : item.label}
              </span>
            </td>
            <td className="ps-3 text-end font-medium whitespace-nowrap text-foreground tabular-nums">
              {formatValue(item.value)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
