import { describe, expect, it } from "vitest";

import { diffJson } from "./json-diff";

describe("diffJson", () => {
  it("classifies changed, unchanged, removed and added leaves", () => {
    const before = { status: "active", plan: { code: "basic", max_streams: 1 }, notes: "vip" };
    const after = { status: "suspended", plan: { code: "basic", max_streams: 2 }, phone: "+966" };
    expect(diffJson(before, after)).toEqual([
      { path: "status", kind: "changed", before: "active", after: "suspended" },
      { path: "plan.code", kind: "unchanged", before: "basic", after: "basic" },
      { path: "plan.max_streams", kind: "changed", before: 1, after: 2 },
      { path: "notes", kind: "removed", before: "vip" },
      { path: "phone", kind: "added", after: "+966" },
    ]);
  });

  it("treats a create as all added and a delete as all removed", () => {
    expect(diffJson(null, { a: 1 })).toEqual([{ path: "a", kind: "added", after: 1 }]);
    expect(diffJson({ a: 1 }, null)).toEqual([{ path: "a", kind: "removed", before: 1 }]);
  });

  it("indexes arrays and keeps empty containers as leaves", () => {
    expect(diffJson({ tags: ["a", "b"], meta: {} }, { tags: ["a"], meta: {} })).toEqual([
      { path: "tags[0]", kind: "unchanged", before: "a", after: "a" },
      { path: "tags[1]", kind: "removed", before: "b" },
      { path: "meta", kind: "unchanged", before: {}, after: {} },
    ]);
  });

  it("notices type changes between equal-looking values", () => {
    expect(diffJson({ port: "80" }, { port: 80 })).toEqual([
      { path: "port", kind: "changed", before: "80", after: 80 },
    ]);
  });

  it("diffs bare values", () => {
    expect(diffJson(true, false)).toEqual([
      { path: "", kind: "changed", before: true, after: false },
    ]);
  });
});
