import { describe, expect, it } from "vitest";

import { resolveEnvironment } from "./environment";

describe("resolveEnvironment", () => {
  it("defaults to development on the dev server and production otherwise", () => {
    expect(resolveEnvironment({ DEV: true })).toBe("development");
    expect(resolveEnvironment({ DEV: false })).toBe("production");
  });

  it("lets a build say which environment it is for", () => {
    expect(resolveEnvironment({ DEV: false, VITE_APP_ENV: "staging" })).toBe("staging");
    expect(resolveEnvironment({ DEV: false, VITE_APP_ENV: " Production " })).toBe("production");
  });

  it("ignores unknown labels", () => {
    expect(resolveEnvironment({ DEV: false, VITE_APP_ENV: "qa" })).toBe("production");
  });
});
