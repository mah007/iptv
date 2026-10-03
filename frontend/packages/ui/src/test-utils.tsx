import { render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { I18nextProvider } from "react-i18next";

import { UiProvider } from "./components/ui-provider";
import { createI18n, type Language } from "./i18n";

/** Render inside the kit's providers (i18n with the `ui` namespace, direction, tooltips). */
export function renderWithUi(
  element: ReactElement,
  { language = "en" }: { language?: Language } = {},
) {
  window.localStorage.setItem("smart-iptv.language", language);
  const i18n = createI18n({ en: {}, ar: {} });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <I18nextProvider i18n={i18n}>
        <UiProvider>{children}</UiProvider>
      </I18nextProvider>
    );
  }
  return { ...render(element, { wrapper: Wrapper }), i18n };
}

/** The item at `index`, failing the test when it is missing. */
export function nth<T>(items: readonly T[], index: number): T {
  const item = items[index];
  if (item === undefined) throw new Error(`Expected an item at index ${String(index)}.`);
  return item;
}
