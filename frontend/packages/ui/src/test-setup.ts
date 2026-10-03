import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import { installDomShims } from "./testing";

installDomShims();

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});
