import { cn, useFormatters } from "@smart-iptv/ui";
import { Star } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export interface TitleMetaProps {
  year?: number | null;
  rating?: number | null;
  runtimeMin?: number | null;
  certification?: string;
  badge?: string | null;
  className?: string;
}

/** "1999 · ★ 8.2 · 2 h 16 min · R15 · FHD": what a viewer scans before choosing. */
export function TitleMeta({
  year,
  rating,
  runtimeMin,
  certification,
  badge,
  className,
}: TitleMetaProps) {
  const { t } = useTranslation();
  const format = useFormatters();
  const parts: { key: string; node: ReactNode }[] = [];
  if (year) parts.push({ key: "year", node: <span className="tabular-nums">{year}</span> });
  if (typeof rating === "number" && rating > 0) {
    parts.push({
      key: "rating",
      node: (
        <span className="inline-flex items-center gap-1 tabular-nums">
          <Star aria-hidden="true" className="size-3.5 fill-warning text-warning" />
          <span className="sr-only">{t("title.rating")}</span>
          {format.number(rating, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}
        </span>
      ),
    });
  }
  if (runtimeMin) {
    parts.push({ key: "runtime", node: <span>{format.duration(runtimeMin * 60, "short")}</span> });
  }
  if (certification) {
    parts.push({
      key: "certification",
      node: (
        <span className="rounded-badge border border-current/40 px-1.5 text-xs leading-5" dir="ltr">
          {certification}
        </span>
      ),
    });
  }
  if (badge) {
    parts.push({
      key: "badge",
      node: (
        <span
          className="rounded-badge bg-foreground/10 px-1.5 text-xs font-semibold leading-5"
          dir="ltr"
        >
          {badge}
        </span>
      ),
    });
  }
  if (parts.length === 0) return null;
  return (
    <p className={cn("flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm", className)}>
      {parts.map((part, index) => (
        <span key={part.key} className="inline-flex items-center gap-2.5">
          {index > 0 ? (
            <span aria-hidden="true" className="opacity-50">
              ·
            </span>
          ) : null}
          {part.node}
        </span>
      ))}
    </p>
  );
}
