import { cn, LanguageToggle, ThemeToggle } from "@smart-iptv/ui";
import { Link, Outlet } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { BrandLockup } from "./brand";
import { LegalLinks } from "./legal-links";
import { SupportContacts } from "./support-contacts";

/** Full-page frame for screens outside the signed-in area: sign-in, passwords, 404, fatal errors. */
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
      <main id="main" className="flex flex-1 items-center justify-center px-4 py-10">
        <div className={cn("w-full max-w-sm", className)}>{children}</div>
      </main>
      <footer className="grid gap-2 px-4 pb-6 text-center text-xs text-muted-foreground">
        <p>{t("auth.footer")}</p>
        <SupportContacts />
        <p>
          <Link to="/about" className="underline-offset-4 hover:text-foreground hover:underline">
            {t("about.link")}
          </Link>
        </p>
        <LegalLinks />
      </footer>
    </div>
  );
}

/** Route layout for /privacy and /terms: the same frame, wide enough to read. */
export function DocumentLayout() {
  return (
    <CenteredLayout className="max-w-3xl">
      <Outlet />
    </CenteredLayout>
  );
}

/** Route layout for /login, /forgot-password and /reset-password. */
export function AuthLayout() {
  return (
    <CenteredLayout>
      <Outlet />
    </CenteredLayout>
  );
}
