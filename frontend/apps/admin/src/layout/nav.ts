import type { LinkProps } from "@tanstack/react-router";
import { House, type LucideIcon } from "lucide-react";

export interface NavItem {
  /** Typed against the route tree, so only routes that exist can be listed. */
  to: NonNullable<LinkProps["to"]>;
  /** Translation key in the app namespace. */
  labelKey: string;
  icon: LucideIcon;
  /** Active only on this exact path (needed for "/"). */
  exact?: boolean;
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
    items: [{ to: "/", labelKey: "nav.home", icon: House, exact: true }],
  },
];
