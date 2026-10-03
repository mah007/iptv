import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { Badge, type BadgeTone } from "./badge";

export type Environment = "development" | "staging" | "production";

const ENVIRONMENT_TONE: Record<Environment, BadgeTone> = {
  development: "info",
  staging: "warning",
  production: "danger",
};

/** Sticky bar above the page content, translucent over scrolled content. */
export function Topbar({ className, ...props }: ComponentProps<"header">) {
  return (
    <header
      data-slot="topbar"
      className={cn(
        "sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b border-border bg-background/85 px-3 backdrop-blur-md md:px-4",
        className,
      )}
      {...props}
    />
  );
}

/** DEV / STAGING / PROD, colour-coded so nobody mistakes production for a sandbox. */
export function EnvironmentBadge({
  environment,
  className,
}: {
  environment: Environment;
  className?: string;
}) {
  const { t } = useTranslation("ui");
  return (
    <Badge
      tone={ENVIRONMENT_TONE[environment]}
      dot
      className={cn("uppercase ltr:tracking-wide", className)}
      title={t(`environment.${environment}Long`)}
    >
      {t(`environment.${environment}`)}
    </Badge>
  );
}
