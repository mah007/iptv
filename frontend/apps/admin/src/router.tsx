import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Outlet,
  redirect,
  type RouterHistory,
} from "@tanstack/react-router";

import { parseAdminsSearch } from "./features/admins/search";
import { parseAuditSearch } from "./features/audit/search";
import { AuthLayout } from "./features/auth/auth-layout";
import { createPendingSignIn, type PendingSignIn } from "./features/auth/pending-sign-in";
import { parseLoginSearch } from "./features/auth/search";
import { parseCategoriesSearch } from "./features/categories/search";
import { parseCustomerSearch, parseCustomersSearch } from "./features/customers/search";
import { parseReviewSearch } from "./features/review/search";
import { parseTitlesSearch } from "./features/titles/search";
import { parseTranscodeSearch } from "./features/transcode/search";
import { AdminLayout } from "./layout/admin-layout";
import { isSignedOutError, meQueryOptions } from "./lib/auth";
import { NotFoundPage } from "./pages/not-found";
import { PageSkeleton } from "./pages/page-skeleton";
import { RootErrorPage, RouteErrorPage } from "./pages/route-error";

export interface RouterContext {
  queryClient: QueryClient;
  /** The authenticator enrolment of a sign-in in progress (memory only). */
  signIn: PendingSignIn;
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

/** Pages inside the admin shell (sidebar + topbar): signed-in admins only. */
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app",
  beforeLoad: async ({ context, location }) => {
    try {
      // Cached data is used as is (static); the signed-in shell refreshes it itself.
      await context.queryClient.query({ ...meQueryOptions(), staleTime: "static" });
    } catch (error) {
      if (isSignedOutError(error)) {
        // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
        throw redirect({ to: "/login", search: { redirect: location.href } });
      }
      throw error;
    }
  },
  // The guard failing (API down) has no shell to render in: full-page error.
  errorComponent: RootErrorPage,
  component: AdminLayout,
});
const dashboardRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/",
  component: lazyRouteComponent(() => import("./pages/dashboard"), "DashboardPage"),
});
const customersRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/customers",
  validateSearch: parseCustomersSearch,
  component: lazyRouteComponent(() => import("./pages/customers"), "CustomersPage"),
});
const customerRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/customers/$customerId",
  validateSearch: parseCustomerSearch,
  component: lazyRouteComponent(() => import("./pages/customer-detail"), "CustomerDetailPage"),
});
const sessionsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/sessions",
  component: lazyRouteComponent(() => import("./pages/sessions"), "SessionsPage"),
});
const librariesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/libraries",
  component: lazyRouteComponent(() => import("./pages/libraries"), "LibrariesPage"),
});
const moviesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/movies",
  validateSearch: parseTitlesSearch,
  component: lazyRouteComponent(() => import("./pages/titles"), "MoviesPage"),
});
const movieRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/movies/$titleId",
  component: lazyRouteComponent(() => import("./pages/title-detail"), "MovieDetailPage"),
});
const seriesListRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/series",
  validateSearch: parseTitlesSearch,
  component: lazyRouteComponent(() => import("./pages/titles"), "SeriesPage"),
});
const seriesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/series/$titleId",
  component: lazyRouteComponent(() => import("./pages/title-detail"), "SeriesDetailPage"),
});
const reviewRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/review",
  validateSearch: parseReviewSearch,
  component: lazyRouteComponent(() => import("./pages/review-queue"), "ReviewQueuePage"),
});
const categoriesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/categories",
  validateSearch: parseCategoriesSearch,
  component: lazyRouteComponent(() => import("./pages/categories"), "CategoriesPage"),
});
const transcodeRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/transcode",
  validateSearch: parseTranscodeSearch,
  component: lazyRouteComponent(() => import("./pages/transcode-jobs"), "TranscodeJobsPage"),
});
const adminsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/admins",
  validateSearch: parseAdminsSearch,
  component: lazyRouteComponent(() => import("./pages/admins"), "AdminsPage"),
});
const auditRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/audit",
  validateSearch: parseAuditSearch,
  component: lazyRouteComponent(() => import("./pages/audit"), "AuditPage"),
});
const settingsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/settings",
  component: lazyRouteComponent(() => import("./pages/settings"), "SettingsPage"),
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
  validateSearch: parseLoginSearch,
  component: lazyRouteComponent(() => import("./pages/login"), "LoginPage"),
});
const mfaRoute = createRoute({
  getParentRoute: () => authRoute,
  path: "/login/mfa",
  validateSearch: parseLoginSearch,
  component: lazyRouteComponent(() => import("./pages/login"), "MfaPage"),
});

export const routeTree = rootRoute.addChildren([
  appRoute.addChildren([
    dashboardRoute,
    customersRoute,
    customerRoute,
    sessionsRoute,
    librariesRoute,
    moviesRoute,
    movieRoute,
    seriesListRoute,
    seriesRoute,
    reviewRoute,
    categoriesRoute,
    transcodeRoute,
    adminsRoute,
    auditRoute,
    settingsRoute,
  ]),
  authRoute.addChildren([loginRoute, mfaRoute]),
]);

export function createAppRouter({
  queryClient,
  signIn = createPendingSignIn(),
  history,
}: {
  queryClient: QueryClient;
  signIn?: PendingSignIn;
  history?: RouterHistory;
}) {
  return createRouter({
    routeTree,
    context: { queryClient, signIn },
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
