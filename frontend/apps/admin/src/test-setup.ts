import { installDomShims } from "@smart-iptv/ui/testing";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

installDomShims();

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.density;
});
