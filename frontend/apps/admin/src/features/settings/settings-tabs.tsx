import { cn } from "@smart-iptv/ui";
import { Link, useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

const TABS = [
  { to: "/settings", key: "general" },
  { to: "/settings/integrations", key: "integrations" },
  { to: "/settings/system", key: "system" },
] as const;

/** The three settings pages: every setting, the integrations, the installation. */
export function SettingsTabs({ className }: { className?: string }) {
  const { t } = useTranslation();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  return (
    <nav aria-label={t("settings.tabs.label")} className={cn("overflow-x-auto", className)}>
      <ul className="flex w-max gap-1 rounded-input bg-muted p-1">
        {TABS.map((tab) => {
          const active = pathname === tab.to;
          return (
            <li key={tab.to}>
              <Link
                to={tab.to}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "block rounded-badge px-3 py-1.5 text-ui font-medium text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
                  active && "bg-background text-foreground shadow-xs",
                )}
              >
                {t(`settings.tabs.${tab.key}`)}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
