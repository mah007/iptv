import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "./badge";
import { LanguageToggle } from "./language-toggle";
import { ThemeToggle } from "./theme-toggle";

export type Environment = "development" | "staging" | "production";

const ENVIRONMENT_TONE = {
  development: "info",
  staging: "warning",
  production: "danger",
} as const;

export interface AppShellProps {
  /** Product area shown next to the brand, e.g. the translated "Admin". */
  area?: ReactNode;
  environment?: Environment;
  children: ReactNode;
}

/** Page frame shared by the admin and portal apps: top bar plus main content. */
export function AppShell({ area, environment, children }: AppShellProps) {
  const { t } = useTranslation("ui");
  return (
    <div className="min-h-dvh bg-background text-foreground">
      <header className="sticky top-0 z-10 border-b border-border bg-background/80 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4">
          <span className="text-base font-semibold tracking-tight">{t("brand")}</span>
          {area ? <span className="text-sm text-muted-foreground">{area}</span> : null}
          {environment && environment !== "production" ? (
            <Badge tone={ENVIRONMENT_TONE[environment]}>{t(`environment.${environment}`)}</Badge>
          ) : null}
          <div className="ms-auto flex items-center gap-1">
            <LanguageToggle />
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
    </div>
  );
}
