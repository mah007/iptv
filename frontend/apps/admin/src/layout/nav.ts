import type { LinkProps } from "@tanstack/react-router";
import {
  Activity,
  Clapperboard,
  FolderTree,
  HardDrive,
  LayoutDashboard,
  ListChecks,
  ScrollText,
  Settings,
  ShieldCheck,
  Tv,
  Users,
  type LucideIcon,
} from "lucide-react";

import { LIBRARY_VIEW, type PermissionRequirement } from "../lib/auth";

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
  /** A live count next to the label (SPEC §8.2: the review queue's open items). */
  badge?: "openReviews";
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
      { to: "/sessions", labelKey: "nav.sessions", icon: Activity, permission: "customers.view" },
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
    id: "content",
    titleKey: "nav.sections.content",
    items: [
      { to: "/libraries", labelKey: "nav.libraries", icon: HardDrive, permission: LIBRARY_VIEW },
      { to: "/movies", labelKey: "nav.movies", icon: Clapperboard, permission: LIBRARY_VIEW },
      { to: "/series", labelKey: "nav.series", icon: Tv, permission: LIBRARY_VIEW },
      {
        to: "/review",
        labelKey: "nav.review",
        icon: ListChecks,
        permission: LIBRARY_VIEW,
        badge: "openReviews",
      },
      {
        to: "/categories",
        labelKey: "nav.categories",
        icon: FolderTree,
        permission: [...LIBRARY_VIEW, "customers.view"],
      },
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
