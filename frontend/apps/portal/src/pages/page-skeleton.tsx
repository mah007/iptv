import { Skeleton } from "@smart-iptv/ui";
import { useTranslation } from "react-i18next";

/** Shown while a page's code or first data loads: the shape of a rail page, never a spinner. */
export function PageSkeleton() {
  const { t } = useTranslation();
  return (
    <div className="page-top px-4 sm:px-6 lg:px-10" aria-busy="true">
      <span className="sr-only" role="status">
        {t("states.loading")}
      </span>
      <Skeleton className="mb-6 h-8 w-48" />
      <div className="flex gap-3 overflow-hidden">
        {Array.from({ length: 8 }, (_, index) => (
          <Skeleton key={index} className="aspect-[2/3] w-32 shrink-0 rounded-card sm:w-40" />
        ))}
      </div>
    </div>
  );
}
