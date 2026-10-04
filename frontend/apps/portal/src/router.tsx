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

import { AuthLayout } from "./layout/auth-layout";
import { PortalLayout } from "./layout/portal-layout";
import { isSignedOutError, meQueryOptions } from "./lib/auth";
import { NotFoundPage } from "./pages/not-found";
import { PageSkeleton } from "./pages/page-skeleton";
import { RootErrorPage, RouteErrorPage } from "./pages/route-error";
import {
  parseBrowseSearch,
  parseLoginSearch,
  parseResetSearch,
  parseSearchSearch,
  parseSeriesSearch,
  parseSubscriptionSearch,
  parseWatchSearch,
} from "./search";

export interface RouterContext {
  queryClient: QueryClient;
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: Outlet,
  notFoundComponent: NotFoundPage,
  errorComponent: RootErrorPage,
});

/*
 * Pages are code-split: each loads its own chunk (the player with Shaka, for
 * one) behind the route skeleton. Layouts, boundaries and 404 stay eager.
 */

/** Signed-in customers only; everything else goes to /login and comes back. */
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app",
  beforeLoad: async ({ context, location }) => {
    try {
      // Cached data is used as is (static); pages refresh it themselves.
      await context.queryClient.query({ ...meQueryOptions(), staleTime: "static" });
    } catch (error) {
      if (isSignedOutError(error)) {
        // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
        throw redirect({ to: "/login", search: { redirect: location.href } });
      }
      throw error;
    }
  },
  errorComponent: RootErrorPage,
  component: Outlet,
});

/** Pages with the portal's navigation. */
const shellRoute = createRoute({
  getParentRoute: () => appRoute,
  id: "shell",
  component: PortalLayout,
});

const homeRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/",
  component: lazyRouteComponent(() => import("./pages/home"), "HomePage"),
});
const moviesRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/movies",
  validateSearch: parseBrowseSearch,
  component: lazyRouteComponent(() => import("./pages/browse"), "MoviesPage"),
});
const movieRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/movies/$titleId",
  component: lazyRouteComponent(() => import("./pages/title-detail"), "MovieDetailPage"),
});
const seriesListRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/series",
  validateSearch: parseBrowseSearch,
  component: lazyRouteComponent(() => import("./pages/browse"), "SeriesPage"),
});
const seriesRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/series/$titleId",
  validateSearch: parseSeriesSearch,
  component: lazyRouteComponent(() => import("./pages/title-detail"), "SeriesDetailPage"),
});
const collectionRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/collections/$slug",
  component: lazyRouteComponent(() => import("./pages/collection"), "CollectionPage"),
});
const personRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/people/$personId",
  component: lazyRouteComponent(() => import("./pages/person"), "PersonPage"),
});
const searchRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/search",
  validateSearch: parseSearchSearch,
  component: lazyRouteComponent(() => import("./pages/search"), "SearchPage"),
});
const myListRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/my-list",
  component: lazyRouteComponent(() => import("./pages/my-list"), "MyListPage"),
});
const historyRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/history",
  component: lazyRouteComponent(() => import("./pages/history"), "HistoryPage"),
});
const accountRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/account",
  component: lazyRouteComponent(() => import("./pages/account"), "AccountPage"),
});
const devicesRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/account/devices",
  component: lazyRouteComponent(() => import("./pages/devices"), "DevicesPage"),
});
const subscriptionRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/account/subscription",
  validateSearch: parseSubscriptionSearch,
  component: lazyRouteComponent(() => import("./pages/subscription"), "SubscriptionPage"),
});
const plansRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/plans",
  component: lazyRouteComponent(() => import("./pages/plans"), "PlansPage"),
});

/** The player takes the whole screen, without the navigation. */
const watchMovieRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/watch/movie/$titleId",
  validateSearch: parseWatchSearch,
  component: lazyRouteComponent(() => import("./pages/watch"), "WatchMoviePage"),
});
const watchSeriesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/watch/series/$titleId",
  validateSearch: parseWatchSearch,
  component: lazyRouteComponent(() => import("./pages/watch"), "WatchSeriesPage"),
});

/** Sign-in and password screens, outside the signed-in area. */
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
const forgotRoute = createRoute({
  getParentRoute: () => authRoute,
  path: "/forgot-password",
  component: lazyRouteComponent(() => import("./pages/login"), "ForgotPasswordPage"),
});
const resetRoute = createRoute({
  getParentRoute: () => authRoute,
  path: "/reset-password",
  validateSearch: parseResetSearch,
  component: lazyRouteComponent(() => import("./pages/login"), "ResetPasswordPage"),
});

export const routeTree = rootRoute.addChildren([
  appRoute.addChildren([
    shellRoute.addChildren([
      homeRoute,
      moviesRoute,
      movieRoute,
      seriesListRoute,
      seriesRoute,
      collectionRoute,
      personRoute,
      searchRoute,
      myListRoute,
      historyRoute,
      accountRoute,
      devicesRoute,
      subscriptionRoute,
      plansRoute,
    ]),
    watchMovieRoute,
    watchSeriesRoute,
  ]),
  authRoute.addChildren([loginRoute, forgotRoute, resetRoute]),
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
