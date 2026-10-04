import { ApiError, type Problem } from "@smart-iptv/api";
import { createI18n } from "@smart-iptv/ui";
import { describe, expect, it, vi } from "vitest";

import ar from "../locales/ar.json";
import en from "../locales/en.json";
import {
  FIELD_CODE_PARAMS,
  applyFieldErrors,
  fieldErrorMessage,
  numbersIn,
  translatedFieldErrors,
} from "./problems";

function i18n(language: "en" | "ar") {
  window.localStorage.setItem("smart-iptv.language", language);
  return createI18n({ en, ar });
}

function validation(
  fieldErrors: Record<string, string[]>,
  codes?: Record<string, string[]>,
): ApiError {
  const problem: Problem = {
    type: "urn:smart-iptv:problem:validation-error",
    title: "Invalid request",
    status: 400,
    code: "VALIDATION_ERROR",
    detail: "Invalid input.",
    field_errors: fieldErrors,
    ...(codes ? { field_error_codes: codes } : {}),
  };
  return new ApiError({
    status: 400,
    code: "VALIDATION_ERROR",
    title: problem.title,
    detail: problem.detail,
    fieldErrors,
    problem,
  });
}

describe("field error codes", () => {
  it("has a message for every translated code in both languages", () => {
    const messages = { en: en.fieldErrors, ar: ar.fieldErrors } as Record<
      string,
      Record<string, string>
    >;
    for (const [code, names] of Object.entries(FIELD_CODE_PARAMS)) {
      for (const language of ["en", "ar"]) {
        const message = messages[language]?.[code];
        expect(message, `${language}: fieldErrors.${code}`).toBeTruthy();
        for (const name of names) expect(message).toContain(`{{${name}}}`);
      }
    }
  });

  it("reads the numbers of an API message in order", () => {
    expect(numbersIn("Use 10 to 64 characters.")).toEqual([10, 64]);
    expect(numbersIn("Ensure this value is less than or equal to 2.5.")).toEqual([2.5]);
    expect(numbersIn("Required.")).toEqual([]);
  });

  it("translates known codes, taking numbers from the form or the message", () => {
    const { t } = i18n("ar");
    expect(fieldErrorMessage(t, "username_taken", "This username is already taken.")).toBe(
      "اسم المستخدم هذا مستخدم بالفعل.",
    );
    expect(fieldErrorMessage(t, "password_length", "Use 10 to 64 characters.")).toBe(
      "استخدم من 10 إلى 64 حرفًا.",
    );
    expect(fieldErrorMessage(t, "max_length", "Too long.", { max: 150 })).toBe(
      "استخدم 150 حرفًا على الأكثر.",
    );
  });

  it("falls back to the API's message for unknown codes and missing numbers", () => {
    const { t } = i18n("ar");
    expect(fieldErrorMessage(t, "invalid", "Enter a valid email address.")).toBe(
      "Enter a valid email address.",
    );
    expect(fieldErrorMessage(t, "toString", "Odd.")).toBe("Odd.");
    expect(fieldErrorMessage(t, undefined, "No code.")).toBe("No code.");
    expect(fieldErrorMessage(t, "max_length", "Too long.")).toBe("Too long.");
  });

  it("puts translated messages on the form and focuses the first", () => {
    const { t } = i18n("ar");
    const setError = vi.fn();
    const error = validation(
      {
        username: ["This username is already taken."],
        "device.password": [
          "Use 8 to 64 characters.",
          "Use only letters, digits and . _ - ~ @ ! *",
        ],
        notes: ["Something odd."],
      },
      {
        username: ["username_taken"],
        "device.password": ["password_length", "password_characters"],
        notes: ["invalid"],
      },
    );
    const applied = applyFieldErrors(
      error,
      setError,
      { username: "username", "device.password": "password", notes: "notes" },
      t,
    );
    expect(applied).toEqual(["username", "password", "notes"]);
    expect(setError).toHaveBeenNthCalledWith(
      1,
      "username",
      { type: "server", message: "اسم المستخدم هذا مستخدم بالفعل." },
      { shouldFocus: true },
    );
    expect(setError).toHaveBeenNthCalledWith(
      2,
      "password",
      { type: "server", message: "استخدم من 8 إلى 64 حرفًا." },
      { shouldFocus: false },
    );
    expect(setError).toHaveBeenNthCalledWith(
      3,
      "notes",
      { type: "server", message: "Something odd." },
      { shouldFocus: false },
    );
  });

  it("keeps the API's messages when an older API sends no codes", () => {
    const { t } = i18n("ar");
    const error = validation({ username: ["This username is already taken."] });
    expect(translatedFieldErrors(t, error)).toEqual(["This username is already taken."]);
    expect(translatedFieldErrors(t, new Error("boom"))).toEqual([]);
  });
});
