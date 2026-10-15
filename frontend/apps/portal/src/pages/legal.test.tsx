import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ME, PLAN } from "../test/fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

const SIGNED_OUT = {
  "GET /api/v1/me": problem(401, "NOT_AUTHENTICATED"),
  "GET /api/v1/auth/csrf": { status: 204 },
};

const PRIVACY = {
  document: "privacy",
  language: "en",
  body: "# Privacy policy\n\nWe keep **little**.\n\n- Email: help@example.com\n",
  updated_at: "2026-10-01T09:00:00Z",
};

describe("legal pages (ADR-0023)", () => {
  it("shows the privacy policy without signing in, in the page's language", async () => {
    const api = mockApi({
      ...SIGNED_OUT,
      "GET /api/v1/legal/privacy": { body: PRIVACY },
    });
    renderApp("/privacy");
    expect(await screen.findByRole("heading", { level: 1, name: "Privacy policy" })).toBeTruthy();
    expect(screen.getByText("little").tagName).toBe("STRONG");
    expect(screen.getByText("Email: help@example.com")).toBeTruthy();
    expect(screen.getByText("Last updated 1 Oct 2026")).toBeTruthy();
    expect(api.sent("GET", "/api/v1/legal/privacy")[0]?.query.get("lang")).toBe("en");
    expect(
      within(screen.getByRole("navigation", { name: "More" }))
        .getByRole("link", { name: "Terms of service" })
        .getAttribute("href"),
    ).toBe("/terms");
  });

  it("asks for the Arabic text in Arabic", async () => {
    const api = mockApi({
      ...SIGNED_OUT,
      "GET /api/v1/legal/terms": {
        body: { ...PRIVACY, document: "terms", language: "ar", body: "# شروط الخدمة\n" },
      },
    });
    renderApp("/terms", { language: "ar" });
    expect(await screen.findByRole("heading", { level: 1, name: "شروط الخدمة" })).toBeTruthy();
    expect(api.sent("GET", "/api/v1/legal/terms")[0]?.query.get("lang")).toBe("ar");
  });

  it("links both pages from the sign-in page", async () => {
    mockApi(SIGNED_OUT);
    renderApp("/login");
    await screen.findByRole("heading", { name: "Sign in" });
    expect(screen.getByRole("link", { name: "Privacy policy" }).getAttribute("href")).toBe(
      "/privacy",
    );
    expect(screen.getByRole("link", { name: "Terms of service" }).getAttribute("href")).toBe(
      "/terms",
    );
  });

  it("says at checkout that paying accepts the terms", async () => {
    mockApi({
      "GET /api/v1/me": { body: ME },
      "GET /api/v1/me/subscription": { body: { subscription: null, pending: null } },
      "GET /api/v1/plans": { body: [PLAN] },
      "GET /api/v1/payment-providers": {
        body: [{ code: "manual", name: "Bank transfer or cash" }],
      },
    });
    const { user } = renderApp("/plans");
    await user.click(await screen.findByRole("button", { name: "Choose" }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => {
      expect(within(dialog).getByRole("link", { name: "Terms of service" })).toBeTruthy();
    });
    expect(within(dialog).getByText(/By continuing you accept the/u)).toBeTruthy();
    expect(
      within(dialog).getByRole("link", { name: "Terms of service" }).getAttribute("target"),
    ).toBe("_blank");
  });
});
