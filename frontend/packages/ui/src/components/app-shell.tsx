import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { LanguageToggle } from "./language-toggle";
import { ThemeToggle } from "./theme-toggle";
import { EnvironmentBadge, Topbar, type Environment } from "./topbar";

export interface AppShellProps {
  /** Product area shown next to the brand, e.g. the translated "Admin". */
  area?: ReactNode;
  /** Shown as a badge outside production. */
  environment?: Environment;
  children: ReactNode;
}

/** Simple frame (top bar + centred content) for apps without a sidebar, like the portal. */
export function AppShell({ area, environment, children }: AppShellProps) {
  const { t } = useTranslation("ui");
  return (
    <div className="min-h-dvh bg-background text-foreground">
      <Topbar className="px-0 md:px-0">
        <div className="mx-auto flex w-full max-w-6xl items-center gap-3 px-4">
          <span className="text-base font-semibold tracking-tight">{t("brand")}</span>
          {area ? <span className="text-sm text-muted-foreground">{area}</span> : null}
          {environment && environment !== "production" ? (
            <EnvironmentBadge environment={environment} />
          ) : null}
          <div className="ms-auto flex items-center gap-1">
            <LanguageToggle />
            <ThemeToggle />
          </div>
        </div>
      </Topbar>
      <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
    </div>
  );
}
