import { Skeleton } from "@smart-iptv/ui";
import { useTranslation } from "react-i18next";

/** Shown while a route loads (SPEC §8.1: skeletons, never a whole-page spinner). */
export function PageSkeleton() {
  const { t } = useTranslation();
  return (
    <div role="status" aria-live="polite" className="grid gap-6">
      <span className="sr-only">{t("layout.loading")}</span>
      <div className="grid gap-2">
        <Skeleton className="h-7 w-56" />
        <Skeleton className="h-4 w-80 max-w-full" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((tile) => (
          <div key={tile} className="grid gap-3 rounded-card border border-border bg-card p-5">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-7 w-20" />
          </div>
        ))}
      </div>
      <div className="grid gap-3 rounded-card border border-border bg-card p-5">
        {[0, 1, 2, 3, 4, 5].map((row) => (
          <Skeleton key={row} className="h-5 w-full" />
        ))}
      </div>
    </div>
  );
}
