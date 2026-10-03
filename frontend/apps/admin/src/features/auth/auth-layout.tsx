import { LanguageToggle, ThemeToggle, cn } from "@smart-iptv/ui";
import { Outlet } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { BrandLockup } from "../../layout/brand";

/** Full-page frame for screens outside the admin shell: sign-in, MFA, 404, fatal errors. */
export function CenteredLayout({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="auth-backdrop relative flex min-h-dvh flex-col bg-background">
      <header className="flex h-14 items-center justify-between gap-3 px-4 sm:px-6">
        <BrandLockup />
        <div className="flex items-center gap-1">
          <LanguageToggle />
          <ThemeToggle />
        </div>
      </header>
      <main className="flex flex-1 items-center justify-center px-4 py-10">
        <div className={cn("w-full max-w-sm", className)}>{children}</div>
      </main>
      <footer className="px-4 pb-6 text-center text-xs text-muted-foreground">
        {t("auth.footer")}
      </footer>
    </div>
  );
}

/** Route layout for /login and /login/mfa. */
export function AuthLayout() {
  return (
    <CenteredLayout>
      <Outlet />
    </CenteredLayout>
  );
}
