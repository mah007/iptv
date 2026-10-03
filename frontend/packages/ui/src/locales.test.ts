import { describe, expect, it } from "vitest";

import ar from "./locales/ar.json";
import en from "./locales/en.json";
import { localeProblems } from "./testing";

describe("ui locales", () => {
  it("Arabic and English define the same messages, with every plural form", () => {
    expect(localeProblems({ en, ar })).toEqual([]);
  });

  it("flags a missing message and a missing Arabic plural form", () => {
    const problems = localeProblems({
      en: { a: "A", items_one: "{{count}} item", items_other: "{{count}} items" },
      ar: { items_one: "عنصر", items_other: "{{count}} عنصر" },
    });
    expect(problems).toContain('ar: missing "a"');
    expect(problems).toContain('ar: "items" lacks the "few" plural form');
    expect(problems).not.toContain('en: "items" lacks the "few" plural form');
  });
});
