import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PageSkeleton } from "./pages/page-skeleton";
import { RouteErrorPage } from "./pages/route-error";
import { renderWithProviders } from "./test-utils";

/** A small router using the app's error and pending components. */
function testRouter(page: { component?: () => never; loader?: () => Promise<void> }) {
  const root = createRootRoute({ component: Outlet });
  const index = createRoute({
    getParentRoute: () => root,
    path: "/",
    component: page.component ?? (() => null),
    ...(page.loader ? { loader: page.loader } : {}),
  });
  return createRouter({
    routeTree: root.addChildren([index]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
    defaultErrorComponent: RouteErrorPage,
    defaultPendingComponent: PageSkeleton,
    defaultPendingMs: 0,
  });
}

describe("route boundaries", () => {
  it("contains a page that crashes and offers a retry and a way home", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const router = testRouter({
      component: () => {
        throw new Error("render failed");
      },
    });
    renderWithProviders(<RouterProvider router={router} />);
    expect(await screen.findByRole("heading", { name: "This page couldn't load" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to home" })).toBeTruthy();
  });

  it("shows a skeleton, not a spinner, while a page loads", async () => {
    const router = testRouter({ loader: () => new Promise<void>(() => undefined) });
    renderWithProviders(<RouterProvider router={router} />);
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("Loading…");
    expect(status.querySelectorAll("[data-slot=skeleton]").length).toBeGreaterThan(0);
  });
});
