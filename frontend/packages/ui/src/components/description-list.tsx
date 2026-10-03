import type { ComponentProps, ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";

export interface DescriptionListProps extends ComponentProps<"dl"> {
  /** Two label/value columns side by side on wide screens. */
  columns?: 1 | 2;
}

/** Dense label/value grid for detail pages; rows follow the density setting. */
export function DescriptionList({ columns = 1, className, ...props }: DescriptionListProps) {
  return (
    <dl
      data-slot="description-list"
      className={cn("grid gap-x-8 text-ui", columns === 2 && "lg:grid-cols-2", className)}
      {...props}
    />
  );
}

export interface DescriptionItemProps extends Omit<ComponentProps<"div">, "children"> {
  label: ReactNode;
  /** The value; empty shows a dash (read out as "Not set"). */
  children?: ReactNode;
}

export function DescriptionItem({ label, children, className, ...props }: DescriptionItemProps) {
  const { t } = useTranslation("ui");
  const empty =
    children === undefined || children === null || children === "" || children === false;
  return (
    <div
      data-slot="description-item"
      className={cn(
        "grid grid-cols-[minmax(7rem,2fr)_5fr] items-baseline gap-3 border-b border-border/70 py-[calc((var(--density-row)-1.25rem)/2)] last:border-b-0",
        className,
      )}
      {...props}
    >
      <dt className="min-w-0 truncate text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-foreground">
        {empty ? (
          <>
            <span aria-hidden="true" className="text-muted-foreground">
              {t("description.empty")}
            </span>
            <span className="sr-only">{t("description.notSet")}</span>
          </>
        ) : (
          children
        )}
      </dd>
    </div>
  );
}
