import { Button, cn, LanguageToggle, ThemeToggle } from "@smart-iptv/ui";
import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { Clapperboard, Heart, House, Search, Tv, UserRound } from "lucide-react";
import { useEffect, useState, type ComponentType } from "react";
import { useTranslation } from "react-i18next";

import { AccountMenu } from "./account-menu";
import { BrandLockup } from "./brand";
import { needsRenewNotice, RenewBanner, useAccessState } from "./renew-banner";

interface NavLink {
  to: "/" | "/movies" | "/series" | "/my-list" | "/search" | "/account";
  label: string;
  icon: ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  exact?: boolean;
}

const NAV: readonly NavLink[] = [
  { to: "/", label: "nav.home", icon: House, exact: true },
  { to: "/movies", label: "nav.movies", icon: Clapperboard },
  { to: "/series", label: "nav.series", icon: Tv },
  { to: "/my-list", label: "nav.myList", icon: Heart },
];

/** Bottom bar on phones: the main sections plus search and the account. */
const MOBILE_NAV: readonly NavLink[] = [
  { to: "/", label: "nav.home", icon: House, exact: true },
  { to: "/movies", label: "nav.movies", icon: Clapperboard },
  { to: "/series", label: "nav.series", icon: Tv },
  { to: "/search", label: "nav.search", icon: Search },
  { to: "/account", label: "nav.account", icon: UserRound },
];

/** The top bar turns opaque once the page scrolls past the hero. */
function useScrolled(threshold = 24): boolean {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const update = () => {
      setScrolled(window.scrollY > threshold);
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    return () => {
      window.removeEventListener("scroll", update);
    };
  }, [threshold]);
  return scrolled;
}

/** The signed-in frame: a top bar over full-bleed pages, and a tab bar on phones. */
export function PortalLayout() {
  const { t } = useTranslation();
  const scrolled = useScrolled();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const access = useAccessState();

  return (
    <div className="min-h-dvh bg-background text-foreground">
      <a
        href="#main"
        className="sr-only z-50 rounded-input bg-primary px-3 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:start-3 focus:top-3"
      >
        {t("nav.skip")}
      </a>
      <header
        data-scrolled={scrolled || undefined}
        className={cn(
          "fixed inset-x-0 top-0 z-40 h-16 transition-colors duration-200 ease-out",
          "bg-linear-to-b from-background/90 to-transparent",
          "data-[scrolled]:border-b data-[scrolled]:border-border data-[scrolled]:bg-background/90 data-[scrolled]:backdrop-blur-md",
        )}
      >
        <div className="mx-auto flex h-full max-w-[1800px] items-center gap-2 px-4 sm:px-6 lg:px-10">
          <Link
            to="/"
            className="me-2 rounded-input outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <BrandLockup compactOnPhones />
          </Link>
          <nav aria-label={t("nav.label")} className="hidden md:block">
            <ul className="flex items-center gap-1">
              {NAV.map((item) => (
                <li key={item.to}>
                  <Link
                    to={item.to}
                    activeOptions={{ exact: item.exact === true }}
                    className="rounded-input px-3 py-2 text-sm font-medium text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring aria-[current=page]:text-foreground"
                  >
                    {t(item.label)}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          <div className="ms-auto flex items-center gap-0.5">
            <Button asChild variant="ghost" size="icon" aria-label={t("nav.search")}>
              <Link to="/search">
                <Search aria-hidden="true" />
              </Link>
            </Button>
            <LanguageToggle className="px-2" />
            <ThemeToggle />
            <AccountMenu />
          </div>
        </div>
      </header>
      <main
        id="main"
        tabIndex={-1}
        data-banner={needsRenewNotice(access) || undefined}
        className="group/main pb-20 outline-none md:pb-10"
      >
        <RenewBanner state={access} />
        <Outlet />
      </main>
      <nav
        aria-label={t("nav.label")}
        className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md md:hidden"
      >
        <ul className="grid grid-cols-5">
          {MOBILE_NAV.map((item) => {
            const Icon = item.icon;
            const active =
              item.exact === true ? pathname === item.to : pathname.startsWith(item.to);
            return (
              <li key={item.to}>
                <Link
                  to={item.to}
                  activeOptions={{ exact: item.exact === true }}
                  className={cn(
                    "flex h-14 flex-col items-center justify-center gap-0.5 text-[0.6875rem] font-medium outline-none focus-visible:bg-accent",
                    active ? "text-primary" : "text-muted-foreground",
                  )}
                >
                  <Icon aria-hidden className="size-5" />
                  {t(item.label)}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
