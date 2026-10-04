import { createMemoryHistory } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App, createAppDependencies } from "../app";

/** The whole portal (router, query client, i18n) at `path`, on the mocked API. */
export function renderApp(path = "/", { language = "en" }: { language?: "en" | "ar" } = {}) {
  window.localStorage.setItem("smart-iptv.language", language);
  const dependencies = createAppDependencies(createMemoryHistory({ initialEntries: [path] }));
  render(<App {...dependencies} />);
  return { ...dependencies, user: userEvent.setup() };
}
