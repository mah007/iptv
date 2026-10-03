import type { LinkProps } from "@tanstack/react-router";
import {
  LayoutDashboard,
  ScrollText,
  Settings,
  ShieldCheck,
  Users,
  type LucideIcon,
} from "lucide-react";

import type { PermissionRequirement } from "../lib/auth";

export interface NavItem {
  /** Typed against the route tree, so only routes that exist can be listed. */
  to: NonNullable<LinkProps["to"]>;
  /** Translation key in the app namespace. */
  labelKey: string;
  icon: LucideIcon;
  /** Active only on this exact path (needed for "/"). */
  exact?: boolean;
  /** Shown only to admins holding this permission (any of several). */
  permission: PermissionRequirement;
}

export interface NavSection {
  id: string;
  titleKey: string;
  items: readonly NavItem[];
}

/**
 * Sidebar navigation (SPEC §8.2 sections). Sections and items are added as
 * their pages ship; listing a page that doesn't exist yet is not allowed.
 */
export const NAV_SECTIONS: readonly NavSection[] = [
  {
    id: "overview",
    titleKey: "nav.sections.overview",
    items: [
      {
        to: "/",
        labelKey: "nav.dashboard",
        icon: LayoutDashboard,
        exact: true,
        permission: "dashboard.view",
      },
    ],
  },
  {
    id: "customers",
    titleKey: "nav.sections.customers",
    items: [
      { to: "/customers", labelKey: "nav.customers", icon: Users, permission: "customers.view" },
    ],
  },
  {
    id: "security",
    titleKey: "nav.sections.security",
    items: [
      {
        to: "/admins",
        labelKey: "nav.admins",
        icon: ShieldCheck,
        permission: ["admins.manage", "roles.manage"],
      },
      { to: "/audit", labelKey: "nav.audit", icon: ScrollText, permission: "audit.view" },
    ],
  },
  {
    id: "system",
    titleKey: "nav.sections.system",
    items: [
      { to: "/settings", labelKey: "nav.settings", icon: Settings, permission: "settings.view" },
    ],
  },
];
