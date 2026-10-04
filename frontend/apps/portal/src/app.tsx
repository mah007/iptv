import { isApiError } from "@smart-iptv/api-portal";
import {
  createI18n,
  ErrorBoundary,
  ErrorState,
  initTheme,
  Toaster,
  UiProvider,
} from "@smart-iptv/ui";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
  type Query,
} from "@tanstack/react-query";
import { RouterProvider, type RouterHistory } from "@tanstack/react-router";
import type { i18n as I18n } from "i18next";
import { I18nextProvider, useTranslation } from "react-i18next";

import { isPublicPath, isSignedOutError } from "./lib/auth";
import ar from "./locales/ar.json";
import en from "./locales/en.json";
import { createAppRouter, type AppRouter } from "./router";

export interface AppDependencies {
  i18n: I18n;
  queryClient: QueryClient;
  router: AppRouter;
}

/** Client errors (4xx) won't change on a retry; network and server errors get one more try. */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

/** Build everything the portal needs, applying theme and language before the first paint. */
export function createAppDependencies(history?: RouterHistory): AppDependencies {
  // Dark by default, like most streaming services (SPEC §9); the toggle is remembered.
  initTheme("dark");
  const i18n = createI18n({ en, ar });

  // The session ended (idle expiry, a password change, signed out elsewhere): any
  // request that finds it gone sends the customer to sign in, then back here.
  function onSignedOut(error: unknown, query?: Query<unknown, unknown>) {
    if (!isSignedOutError(error)) return;
    if (query?.meta?.signInCheck === true) return; // the route guard redirects itself
    const { pathname, href } = router.state.location;
    if (isPublicPath(pathname)) return;
    void router.navigate({ to: "/login", search: { redirect: href, expired: true } }).then(() => {
      // Nothing of the previous session stays cached.
      queryClient.clear();
    });
  }
  const queryClient = new QueryClient({
    queryCache: new QueryCache({ onError: onSignedOut }),
    mutationCache: new MutationCache({
      onError: (error) => {
        onSignedOut(error);
      },
    }),
    defaultOptions: {
      queries: { staleTime: 60_000, retry: shouldRetry, refetchOnWindowFocus: false },
    },
  });
  const router = createAppRouter({ queryClient, ...(history ? { history } : {}) });
  return { i18n, queryClient, router };
}

/** Last resort if the router itself fails: no router links here, a plain reload. */
function FatalError() {
  const { t } = useTranslation();
  return (
    <div className="grid min-h-dvh place-items-center bg-background px-4">
      <ErrorState
        title={t("routeError.title")}
        description={t("routeError.description")}
        onRetry={() => {
          window.location.reload();
        }}
      />
    </div>
  );
}

export function App({ i18n, queryClient, router }: AppDependencies) {
  return (
    <I18nextProvider i18n={i18n}>
      <UiProvider>
        <QueryClientProvider client={queryClient}>
          <ErrorBoundary fallback={() => <FatalError />}>
            <RouterProvider router={router} />
          </ErrorBoundary>
        </QueryClientProvider>
        <Toaster />
      </UiProvider>
    </I18nextProvider>
  );
}
