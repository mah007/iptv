import { localeProblems } from "@smart-iptv/ui/testing";
import { describe, expect, it } from "vitest";

import ar from "./locales/ar.json";
import en from "./locales/en.json";

describe("admin locales", () => {
  // English is the fallback, so a message missing from Arabic would silently show English.
  it("Arabic and English define the same messages, with every plural form", () => {
    expect(localeProblems({ en, ar })).toEqual([]);
  });
});
