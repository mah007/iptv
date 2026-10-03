import { Inbox, RefreshCw, TriangleAlert } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { Button } from "./button";

interface StateIllustrationProps {
  icon: ReactNode;
  tone?: "neutral" | "danger";
}

/** A small stacked-tiles illustration around an icon; decorative only. */
function StateIllustration({ icon, tone = "neutral" }: StateIllustrationProps) {
  return (
    <div aria-hidden="true" className="relative mb-1 grid size-14 place-items-center">
      <span className="absolute inset-1 rotate-[-8deg] rounded-card border border-border bg-muted/60" />
      <span className="absolute inset-1 rotate-[6deg] rounded-card border border-border bg-muted/80" />
      <span
        className={cn(
          "relative grid size-11 place-items-center rounded-card border border-border bg-card shadow-xs [&_svg]:size-5",
          tone === "danger" ? "text-danger" : "text-muted-foreground",
        )}
      >
        {icon}
      </span>
    </div>
  );
}

export interface EmptyStateProps extends Omit<ComponentProps<"div">, "title"> {
  title: ReactNode;
  description?: ReactNode;
  /** Defaults to an inbox. */
  icon?: ReactNode;
  /** Call to action, e.g. "Create customer". */
  action?: ReactNode;
}

export function EmptyState({
  title,
  description,
  icon,
  action,
  className,
  ...props
}: EmptyStateProps) {
  return (
    <div
      data-slot="empty-state"
      className={cn(
        "flex flex-col items-center justify-center gap-2 px-6 py-12 text-center",
        className,
      )}
      {...props}
    >
      <StateIllustration icon={icon ?? <Inbox />} />
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {description ? <p className="max-w-sm text-ui text-muted-foreground">{description}</p> : null}
      {action ? <div className="mt-2 flex items-center gap-2">{action}</div> : null}
    </div>
  );
}

export interface ErrorStateProps extends Omit<ComponentProps<"div">, "title"> {
  title?: ReactNode;
  description?: ReactNode;
  /** Shows a "Try again" button. */
  onRetry?: (() => void) | undefined;
  /** Technical detail, shown collapsed in development builds only. */
  error?: unknown;
  action?: ReactNode;
}

function errorDetail(error: unknown): string | null {
  if (error instanceof Error) return error.stack ?? `${error.name}: ${error.message}`;
  if (typeof error === "string") return error;
  return null;
}

/** Something failed: says so plainly, offers a retry, never leaks internals in production. */
export function ErrorState({
  title,
  description,
  onRetry,
  error,
  action,
  className,
  ...props
}: ErrorStateProps) {
  const { t } = useTranslation("ui");
  const detail = import.meta.env.DEV ? errorDetail(error) : null;
  return (
    <div
      role="alert"
      data-slot="error-state"
      className={cn(
        "flex flex-col items-center justify-center gap-2 px-6 py-12 text-center",
        className,
      )}
      {...props}
    >
      <StateIllustration icon={<TriangleAlert />} tone="danger" />
      <h3 className="text-sm font-semibold text-foreground">{title ?? t("error.title")}</h3>
      <p className="max-w-sm text-ui text-muted-foreground">
        {description ?? t("error.description")}
      </p>
      {onRetry || action ? (
        <div className="mt-2 flex items-center gap-2">
          {onRetry ? (
            <Button variant="secondary" size="sm" onClick={onRetry}>
              <RefreshCw aria-hidden="true" />
              {t("error.retry")}
            </Button>
          ) : null}
          {action}
        </div>
      ) : null}
      {detail ? (
        <details className="mt-3 w-full max-w-xl text-start">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            {t("error.details")}
          </summary>
          <pre className="mt-2 max-h-48 overflow-auto rounded-input border border-border bg-muted/50 p-3 text-xs leading-5 text-muted-foreground">
            {detail}
          </pre>
        </details>
      ) : null}
    </div>
  );
}
