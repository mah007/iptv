import { createI18n, UiProvider, type Language } from "@smart-iptv/ui";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement, ReactNode } from "react";
import { I18nextProvider } from "react-i18next";

import ar from "./locales/ar.json";
import en from "./locales/en.json";

/** Render a component with the admin's translations and the UI kit providers. */
export function renderWithProviders(
  element: ReactElement,
  { language = "en" }: { language?: Language } = {},
) {
  window.localStorage.setItem("smart-iptv.language", language);
  const i18n = createI18n({ en, ar });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <I18nextProvider i18n={i18n}>
        <UiProvider>{children}</UiProvider>
      </I18nextProvider>
    );
  }
  return { ...render(element, { wrapper: Wrapper }), i18n, user: userEvent.setup() };
}
