import { describe, expect, it } from "vitest";

import {
  accessPatch,
  createCustomerRequest,
  expiresAt,
  wizardSchema,
  type WizardInput,
} from "../features/customers/schemas";
import { hasPermission, safeRedirect } from "./auth";
import { compact, enumParam, intParam, stringParam } from "./search";
import { addMonths, daysUntil, endOfDayIn, isoDateIn, startOfDayIn } from "./time";

describe("time-zone dates", () => {
  it("finds the start and end of a day in Riyadh (UTC+3)", () => {
    expect(startOfDayIn("2026-11-04", "Asia/Riyadh")).toBe("2026-11-03T21:00:00.000Z");
    expect(endOfDayIn("2026-11-04", "Asia/Riyadh")).toBe("2026-11-04T20:59:59.000Z");
  });

  it("follows daylight saving time where a zone has it", () => {
    // London is UTC+1 in July and UTC+0 in January.
    expect(endOfDayIn("2026-07-01", "Europe/London")).toBe("2026-07-01T22:59:59.000Z");
    expect(endOfDayIn("2026-01-01", "Europe/London")).toBe("2026-01-01T23:59:59.000Z");
  });

  it("reads the calendar date of an instant in a zone", () => {
    expect(isoDateIn("2026-11-03T22:30:00Z", "Asia/Riyadh")).toBe("2026-11-04");
    expect(isoDateIn("2026-11-03T22:30:00Z", "UTC")).toBe("2026-11-03");
  });

  it("adds calendar months, clamping to shorter months", () => {
    expect(addMonths("2026-01-31", 1)).toBe("2026-02-28");
    expect(addMonths("2026-10-04", 3)).toBe("2027-01-04");
    expect(addMonths("2024-02-29", 12)).toBe("2025-02-28");
  });

  it("counts whole days left, rounding up", () => {
    const now = Date.parse("2026-10-04T12:00:00Z");
    expect(daysUntil("2026-10-05T11:00:00Z", now)).toBe(1);
    expect(daysUntil("2026-10-01T12:00:00Z", now)).toBe(-3);
  });

  it("refuses a malformed date", () => {
    expect(() => endOfDayIn("04/11/2026", "Asia/Riyadh")).toThrow(RangeError);
  });
});

describe("sign-in helpers", () => {
  it("only returns to paths on this site", () => {
    expect(safeRedirect("/customers?access=expired")).toBe("/customers?access=expired");
    expect(safeRedirect(undefined)).toBe("/");
    expect(safeRedirect("https://evil.example")).toBe("/");
    expect(safeRedirect("//evil.example")).toBe("/");
    expect(safeRedirect("/\\evil.example")).toBe("/");
    expect(safeRedirect("/login/mfa")).toBe("/");
  });

  it("allows an action when any one of its permissions is held", () => {
    const admin = {
      id: "a",
      username: "a",
      name: "",
      email: "",
      locale: "en",
      timezone: "Asia/Riyadh",
      mfa_enabled: true,
      roles: [],
      permissions: ["roles.manage"],
    } as const;
    expect(hasPermission(admin, ["admins.manage", "roles.manage"])).toBe(true);
    expect(hasPermission(admin, "admins.manage")).toBe(false);
    expect(hasPermission(undefined, "roles.manage")).toBe(false);
  });
});

describe("URL search params", () => {
  it("keeps known values and drops junk", () => {
    expect(stringParam(" sara ")).toBe("sara");
    expect(stringParam(123)).toBe("123");
    expect(stringParam("")).toBeUndefined();
    expect(enumParam("expired", ["active", "expired"] as const)).toBe("expired");
    expect(enumParam("nope", ["active", "expired"] as const)).toBeUndefined();
    expect(intParam("7", [7, 14, 30])).toBe(7);
    expect(intParam(8, [7, 14, 30])).toBeUndefined();
    expect(compact({ a: 1, b: undefined })).toEqual({ a: 1 });
  });
});

describe("customer forms", () => {
  const input: WizardInput = {
    profile: { name: " Sara ", email: "", phone: "050-123 4567", locale: "ar", notes: "" },
    access: {
      expiry: "custom",
      expiryDate: "2026-12-31",
      max_streams: "2",
      max_devices: "3",
      max_quality: "720",
      concurrency_policy: "kick_oldest",
      allow_movies: true,
      allow_series: false,
      allow_live: true,
      limitCategories: true,
      category_ids: ["cat-1"],
    },
    device: { create: true, name: "", app_hint: "smarters" },
  };

  it("builds the create request: typed numbers, end-of-day expiry, no blank device name", () => {
    const values = wizardSchema.parse(input);
    expect(createCustomerRequest(values, "Asia/Riyadh")).toEqual({
      name: "Sara",
      email: "",
      phone: "0501234567",
      locale: "ar",
      notes: "",
      timezone: "Asia/Riyadh",
      access: {
        expires_at: "2026-12-31T20:59:59.000Z",
        max_streams: 2,
        max_devices: 3,
        max_quality: 720,
        concurrency_policy: "kick_oldest",
        allow_movies: true,
        allow_series: false,
        allow_live: true,
        category_ids: ["cat-1"],
      },
      device: { app_hint: "smarters" },
    });
  });

  it("rejects limits out of range and a category limit with no category", () => {
    const result = wizardSchema.safeParse({
      ...input,
      access: { ...input.access, max_streams: "0", category_ids: [] },
    });
    expect(result.success).toBe(false);
    const messages = result.error?.issues.map((issue) => issue.message);
    expect(messages).toContain("customers.validation.limitRange");
    expect(messages).toContain("customers.validation.categories");
  });

  it("computes preset expiries from today in the admin's zone", () => {
    const now = new Date("2026-10-04T22:30:00Z"); // already 5 October in Riyadh
    expect(expiresAt({ expiry: "1m", expiryDate: "" }, "Asia/Riyadh", now)).toBe(
      "2026-11-05T20:59:59.000Z",
    );
    expect(expiresAt({ expiry: "none", expiryDate: "" }, "Asia/Riyadh", now)).toBeNull();
  });

  it("patches only the access fields the admin changed", () => {
    const values = wizardSchema.parse(input).access;
    expect(accessPatch(values, { max_devices: true }, "Asia/Riyadh")).toEqual({ max_devices: 3 });
    expect(accessPatch(values, { limitCategories: true }, "Asia/Riyadh")).toEqual({
      category_ids: ["cat-1"],
    });
    expect(accessPatch(values, { expiryDate: true }, "Asia/Riyadh")).toEqual({
      expires_at: "2026-12-31T20:59:59.000Z",
    });
    expect(accessPatch(values, {}, "Asia/Riyadh")).toEqual({});
  });
});
