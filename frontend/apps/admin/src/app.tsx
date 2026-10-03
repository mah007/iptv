import {
  createI18n,
  ErrorBoundary,
  ErrorState,
  initDensity,
  initTheme,
  Toaster,
  UiProvider,
} from "@smart-iptv/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, type RouterHistory } from "@tanstack/react-router";
import type { i18n as I18n } from "i18next";
import { I18nextProvider, useTranslation } from "react-i18next";

import ar from "./locales/ar.json";
import en from "./locales/en.json";
import { createAppRouter, type AppRouter } from "./router";

export interface AppDependencies {
  i18n: I18n;
  queryClient: QueryClient;
  router: AppRouter;
}

/** Build everything the app needs, applying theme, density and language before the first paint. */
export function createAppDependencies(history?: RouterHistory): AppDependencies {
  initTheme("system");
  initDensity();
  const i18n = createI18n({ en, ar });
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: true },
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
