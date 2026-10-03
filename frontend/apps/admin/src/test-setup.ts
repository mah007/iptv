import { installDomShims } from "@smart-iptv/ui/testing";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

installDomShims();

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
  document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.density;
});
