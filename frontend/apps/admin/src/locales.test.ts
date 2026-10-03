import { describe, expect, it } from "vitest";

import ar from "./locales/ar.json";
import en from "./locales/en.json";

/** Dotted paths of every leaf string, e.g. "home.title". */
function leafKeys(messages: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(messages).flatMap(([key, value]) =>
    value !== null && typeof value === "object"
      ? leafKeys(value as Record<string, unknown>, `${prefix}${key}.`)
      : [`${prefix}${key}`],
  );
}

describe("locales", () => {
  // English is the fallback, so a key missing from Arabic would silently show English.
  it("Arabic and English define exactly the same keys", () => {
    expect(leafKeys(ar).sort()).toEqual(leafKeys(en).sort());
  });
});
