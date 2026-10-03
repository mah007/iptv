import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Outlet,
  type RouterHistory,
} from "@tanstack/react-router";

import { AuthLayout } from "./features/auth/auth-layout";
import { AdminLayout } from "./layout/admin-layout";
import { NotFoundPage } from "./pages/not-found";
import { PageSkeleton } from "./pages/page-skeleton";
import { RootErrorPage, RouteErrorPage } from "./pages/route-error";

export interface RouterContext {
  queryClient: QueryClient;
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: Outlet,
  notFoundComponent: NotFoundPage,
  errorComponent: RootErrorPage,
});

/*
 * Pages are code-split: each loads its own chunk (with libraries only it
 * uses) behind the route skeleton. The shell, boundaries and 404 stay eager.
 */

/** Pages inside the admin shell (sidebar + topbar). */
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app",
  component: AdminLayout,
});
const homeRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/",
  component: lazyRouteComponent(() => import("./pages/home"), "HomePage"),
});

/** Sign-in screens, outside the shell. */
const authRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "auth",
  component: AuthLayout,
});
const loginRoute = createRoute({
  getParentRoute: () => authRoute,
  path: "/login",
  component: lazyRouteComponent(() => import("./pages/login"), "LoginPage"),
});
const mfaRoute = createRoute({
  getParentRoute: () => authRoute,
  path: "/login/mfa",
  component: lazyRouteComponent(() => import("./pages/login"), "MfaPage"),
});

export const routeTree = rootRoute.addChildren([
  appRoute.addChildren([homeRoute]),
  authRoute.addChildren([loginRoute, mfaRoute]),
]);

export function createAppRouter({
  queryClient,
  history,
}: {
  queryClient: QueryClient;
  history?: RouterHistory;
}) {
  return createRouter({
    routeTree,
    context: { queryClient },
    defaultPreload: "intent",
    // Route data comes from TanStack Query, which has its own cache.
    defaultPreloadStaleTime: 0,
    defaultPendingComponent: PageSkeleton,
    defaultPendingMs: 200,
    defaultPendingMinMs: 300,
    defaultErrorComponent: RouteErrorPage,
    // Unknown paths always get the full-page 404, outside the shell.
    notFoundMode: "root",
    scrollRestoration: true,
    ...(history ? { history } : {}),
  });
}

export type AppRouter = ReturnType<typeof createAppRouter>;

declare module "@tanstack/react-router" {
  interface Register {
    router: AppRouter;
  }
}
