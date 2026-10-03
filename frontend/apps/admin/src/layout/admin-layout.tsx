import {
  DensityToggle,
  EnvironmentBadge,
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
} from "@smart-iptv/ui";
import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { environment } from "../environment";
import { BrandLockup, BrandMark } from "./brand";
import { NAV_SECTIONS } from "./nav";
import { UserMenu } from "./user-menu";

function AdminSidebar() {
  const { t } = useTranslation();
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
        {NAV_SECTIONS.map((section) => (
          <SidebarSection key={section.id} title={t(section.titleKey)}>
            {section.items.map((item) => (
              <SidebarItem key={item.to} asChild icon={<item.icon />} label={t(item.labelKey)}>
                <Link to={item.to} activeOptions={{ exact: item.exact ?? false }} />
              </SidebarItem>
            ))}
          </SidebarSection>
        ))}
      </SidebarContent>
    </Sidebar>
  );
}

/** The signed-in admin frame: collapsible sidebar, topbar, page content. */
export function AdminLayout() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
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
          <div className="ms-auto flex items-center gap-1">
            <EnvironmentBadge environment={environment} className="me-1 hidden sm:inline-flex" />
            <LanguageToggle />
            <DensityToggle />
            <ThemeToggle />
            <Separator orientation="vertical" className="mx-1 h-5" />
            <UserMenu
              user={null}
              onSignOut={() => {
                void navigate({ to: "/login" });
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
    </SidebarProvider>
  );
}
