import { createI18n, initTheme } from "@smart-iptv/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { I18nextProvider } from "react-i18next";

import ar from "./locales/ar.json";
import en from "./locales/en.json";
import { createAppRouter } from "./router";

initTheme("system");
const i18n = createI18n({ en, ar });
const queryClient = new QueryClient();
const router = createAppRouter();

export function App() {
  return (
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </I18nextProvider>
  );
}
