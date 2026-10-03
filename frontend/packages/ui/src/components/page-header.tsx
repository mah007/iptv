import { ChevronRight } from "lucide-react";
import { Slot } from "radix-ui";
import type { ComponentProps, ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";

export interface PageHeaderProps extends Omit<ComponentProps<"header">, "title"> {
  title: ReactNode;
  description?: ReactNode;
  /** A <Breadcrumb> trail shown above the title. */
  breadcrumbs?: ReactNode;
  /** Primary and secondary actions, aligned to the inline end. */
  actions?: ReactNode;
}

export function PageHeader({
  title,
  description,
  breadcrumbs,
  actions,
  className,
  ...props
}: PageHeaderProps) {
  return (
    <header
      data-slot="page-header"
      className={cn(
        "flex flex-col gap-4 pb-(--density-page) sm:flex-row sm:items-end sm:justify-between",
        className,
      )}
      {...props}
    >
      <div className="grid min-w-0 gap-1.5">
        {breadcrumbs}
        <h1 className="text-xl font-semibold leading-7 text-foreground ltr:tracking-tight">
          {title}
        </h1>
        {description ? (
          <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function Breadcrumb({ className, ...props }: ComponentProps<"nav">) {
  const { t } = useTranslation("ui");
  return <nav aria-label={t("breadcrumb.label")} className={className} {...props} />;
}

export function BreadcrumbList({ className, ...props }: ComponentProps<"ol">) {
  return (
    <ol
      className={cn("flex flex-wrap items-center gap-1 text-ui text-muted-foreground", className)}
      {...props}
    />
  );
}

export function BreadcrumbItem({ className, ...props }: ComponentProps<"li">) {
  return <li className={cn("inline-flex items-center gap-1", className)} {...props} />;
}

/** A crumb that navigates; pass `asChild` with the router's link. */
export function BreadcrumbLink({
  asChild = false,
  className,
  ...props
}: ComponentProps<"a"> & { asChild?: boolean }) {
  const Component = asChild ? Slot.Root : "a";
  return (
    <Component
      className={cn("rounded-[4px] transition-colors hover:text-foreground", className)}
      {...props}
    />
  );
}

export function BreadcrumbPage({ className, ...props }: ComponentProps<"span">) {
  return (
    <span aria-current="page" className={cn("font-medium text-foreground", className)} {...props} />
  );
}

export function BreadcrumbSeparator({ className, children, ...props }: ComponentProps<"li">) {
  return (
    <li
      role="presentation"
      aria-hidden="true"
      className={cn("[&>svg]:size-3.5 [&>svg]:rtl:rotate-180", className)}
      {...props}
    >
      {children ?? <ChevronRight />}
    </li>
  );
}
