import {
  Button,
  DensityToggle,
  EnvironmentBadge,
  Kbd,
  LanguageToggle,
  Separator,
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarInset,
  SidebarItem,
  SidebarProvider,
  SidebarSection,
  SidebarTrigger,
  ThemeToggle,
  Topbar,
  useFormatters,
} from "@smart-iptv/ui";
import { useAuthLogout, useReviewQueueList } from "@smart-iptv/api";
import { useQueryClient } from "@tanstack/react-query";
import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { Search } from "lucide-react";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { environment } from "../environment";
import { LIBRARY_VIEW, useCan, useMe } from "../lib/auth";
import { notifyError } from "../lib/problems";
import {
  ShortcutsProvider,
  focusFirstRow,
  focusPageSearch,
  keyCaps,
  type Shortcut,
} from "../lib/shortcuts";
import { BrandLockup, BrandMark } from "./brand";
import { CommandPalette } from "./command-palette";
import { IdleTimeout } from "./idle-timeout";
import { NAV_SECTIONS, NAV_SHORTCUTS, type NavItem } from "./nav";
import { ShortcutsHelp } from "./shortcuts-help";
import { AlertsMenu, LiveStreams } from "./topbar-status";
import { UserMenu } from "./user-menu";

/** How often the sidebar's counts refresh. */
const BADGE_REFRESH_MS = 60_000;

/** Live counts for sidebar items (SPEC §8.2): the open review queue. */
function useNavBadges(): Record<NonNullable<NavItem["badge"]>, number | undefined> {
  const can = useCan();
  const reviews = useReviewQueueList(
    { status: "open", page_size: 1 },
    { query: { enabled: can(LIBRARY_VIEW), refetchInterval: BADGE_REFRESH_MS } },
  );
  return { openReviews: reviews.data?.count };
}

function AdminSidebar() {
  const { t } = useTranslation();
  const can = useCan();
  const format = useFormatters();
  const badges = useNavBadges();
  const sections = NAV_SECTIONS.map((section) => ({
    ...section,
    items: section.items.filter((item) => can(item.permission)),
  })).filter((section) => section.items.length > 0);
  return (
    <Sidebar label={t("nav.label")}>
      <SidebarHeader>
        <Link
          to="/"
          className="flex min-w-0 items-center rounded-input outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={t("nav.homeLink")}
        >
          <span className="group-data-[collapsed=true]/sidebar:hidden">
            <BrandLockup />
          </span>
          <span className="hidden group-data-[collapsed=true]/sidebar:inline-flex">
            <BrandMark />
          </span>
        </Link>
      </SidebarHeader>
      <SidebarContent>
        {sections.map((section) => (
          <SidebarSection key={section.id} title={t(section.titleKey)}>
            {section.items.map((item) => {
              const count = item.badge ? badges[item.badge] : undefined;
              return (
                <SidebarItem
                  key={item.to}
                  asChild
                  icon={<item.icon />}
                  label={t(item.labelKey)}
                  {...(count ? { badge: format.number(count) } : {})}
                >
                  <Link to={item.to} activeOptions={{ exact: item.exact ?? false }} />
                </SidebarItem>
              );
            })}
          </SidebarSection>
        ))}
      </SidebarContent>
    </Sidebar>
  );
}

/** App-wide shortcuts (SPEC §8.2): palette, help, search, "go to" and "new customer". */
function useGlobalShortcuts({
  openPalette,
  openHelp,
}: {
  openPalette: () => void;
  openHelp: () => void;
}): Shortcut[] {
  const navigate = useNavigate();
  const can = useCan();
  const goTo: Shortcut[] = NAV_SECTIONS.flatMap((section) => section.items)
    .filter((item) => NAV_SHORTCUTS[item.to] !== undefined && can(item.permission))
    .map((item) => ({
      keys: NAV_SHORTCUTS[item.to] ?? "",
      labelKey: `shortcuts.goTo.${item.labelKey}`,
      group: "navigation",
      run: () => {
        void navigate({ to: item.to });
      },
    }));
  return [
    { keys: "mod+k", labelKey: "shortcuts.palette", group: "general", run: openPalette },
    { keys: "?", labelKey: "shortcuts.help", group: "general", run: openHelp },
    {
      keys: "/",
      labelKey: "shortcuts.search",
      group: "general",
      run: () => {
        if (!focusPageSearch()) openPalette();
      },
    },
    {
      keys: "j",
      labelKey: "shortcuts.rows",
      group: "general",
      run: () => {
        focusFirstRow();
      },
    },
    ...(can("customers.edit")
      ? [
          {
            keys: "n c",
            labelKey: "shortcuts.newCustomer",
            group: "general" as const,
            run: () => {
              void navigate({ to: "/customers", search: { new: true } });
            },
          },
        ]
      : []),
    ...goTo,
  ];
}

/** The signed-in admin frame: collapsible sidebar, topbar, page content. */
export function AdminLayout() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const me = useMe();
  const [palette, setPalette] = useState(false);
  const [help, setHelp] = useState(false);
  const logout = useAuthLogout({
    mutation: {
      onSuccess: async () => {
        await navigate({ to: "/login" });
        // Nothing of this session (customer data included) stays in memory.
        queryClient.clear();
      },
      onError: (error) => {
        notifyError(t, error);
      },
    },
  });
  const signOut = useCallback(() => {
    logout.mutate();
  }, [logout]);
  const shortcuts = useGlobalShortcuts({
    openPalette: () => {
      setHelp(false);
      setPalette(true);
    },
    openHelp: () => {
      setPalette(false);
      setHelp(true);
    },
  });
  return (
    <ShortcutsProvider globals={shortcuts}>
      <SidebarProvider>
        <a
          href="#main"
          className="sr-only z-50 rounded-input bg-primary px-3 py-2 text-ui font-medium text-primary-foreground focus:not-sr-only focus:fixed focus:start-3 focus:top-3"
        >
          {t("layout.skipToContent")}
        </a>
        <AdminSidebar />
        <SidebarInset>
          <Topbar>
            <SidebarTrigger />
            <Link
              to="/"
              className="rounded-input outline-none focus-visible:ring-2 focus-visible:ring-ring md:hidden"
              aria-label={t("nav.homeLink")}
            >
              <BrandMark className="size-6" />
            </Link>
            <Button
              variant="outline"
              size="sm"
              className="ms-1 hidden w-56 justify-start gap-2 text-muted-foreground md:inline-flex"
              onClick={() => {
                setPalette(true);
              }}
            >
              <Search aria-hidden="true" />
              <span className="flex-1 text-start">{t("palette.open")}</span>
              <Kbd>{keyCaps("mod+k").flat().join(" ")}</Kbd>
            </Button>
            <div className="ms-auto flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon-sm"
                className="md:hidden"
                aria-label={t("palette.open")}
                onClick={() => {
                  setPalette(true);
                }}
              >
                <Search aria-hidden="true" />
              </Button>
              <EnvironmentBadge environment={environment} className="me-1 hidden sm:inline-flex" />
              <LiveStreams />
              <AlertsMenu />
              <LanguageToggle />
              {/* Density matters on wide tables; phones keep the room for the account menu. */}
              <span className="hidden sm:contents">
                <DensityToggle />
              </span>
              <ThemeToggle />
              <Separator orientation="vertical" className="mx-1 hidden h-5 sm:block" />
              <UserMenu
                user={me ? { name: me.name || me.username, email: me.email || me.username } : null}
                onSignOut={signOut}
                onShowShortcuts={() => {
                  setHelp(true);
                }}
              />
            </div>
          </Topbar>
          <main id="main" tabIndex={-1} className="flex-1 p-(--density-page) outline-none">
            <div className="mx-auto w-full max-w-[1400px]">
              <Outlet />
            </div>
          </main>
        </SidebarInset>
        <CommandPalette
          open={palette}
          onOpenChange={setPalette}
          onShowShortcuts={() => {
            setHelp(true);
          }}
        />
        <ShortcutsHelp open={help} onOpenChange={setHelp} />
        <IdleTimeout onSignOut={signOut} />
      </SidebarProvider>
    </ShortcutsProvider>
  );
}
