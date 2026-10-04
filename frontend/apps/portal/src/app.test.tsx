import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ACCESS, first, HOME, MATRIX, ME } from "./test/fixtures";
import { mockApi, problem } from "./test/mock-api";
import { renderApp } from "./test/render-app";

const SIGNED_IN = {
  "GET /api/v1/me": { body: ME },
  "GET /api/v1/me/subscription": { body: { subscription: null, pending: null } },
  "GET /api/v1/home": { body: HOME },
};

describe("portal", () => {
  it("sends signed-out visitors to sign in, then back to where they were going", async () => {
    let signedIn = false;
    const api = mockApi({
      ...SIGNED_IN,
      "GET /api/v1/me": () => (signedIn ? { body: ME } : problem(401, "NOT_AUTHENTICATED")),
      "GET /api/v1/auth/csrf": { status: 204 },
      "POST /api/v1/auth/login": () => {
        signedIn = true;
        return { body: ME };
      },
    });
    const { user, router } = renderApp("/");

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(router.state.location.search).toEqual({ redirect: "/" });

    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter your username, email or phone number.")).toBeTruthy();

    await user.type(screen.getByLabelText("Username, email or phone"), "layla");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Continue watching" })).toBeTruthy();
    expect(api.sent("POST", "/api/v1/auth/login")[0]?.body).toEqual({
      login: "layla",
      password: "correct horse battery",
    });
  });

  it("explains a refused sign-in without saying which half was wrong", async () => {
    mockApi({
      "GET /api/v1/me": problem(401, "NOT_AUTHENTICATED"),
      "POST /api/v1/auth/login": problem(401, "INVALID_CREDENTIALS"),
    });
    const { user } = renderApp("/login");
    await user.type(await screen.findByLabelText("Username, email or phone"), "layla");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(/don't match/u)).toBeTruthy();
  });

  it("shows the home rows: hero, continue watching with progress, rows with see-all links", async () => {
    mockApi(SIGNED_IN);
    renderApp("/");

    const hero = await screen.findByRole("region", { name: "Featured" });
    expect(within(hero).getByRole("heading", { name: MATRIX.title })).toBeTruthy();
    expect(within(hero).getByRole("link", { name: "Play" }).getAttribute("href")).toBe(
      "/watch/movie/matrix",
    );

    const continueRow = screen.getByRole("region", { name: "Continue watching" });
    const resume = within(continueRow).getByRole("link", { name: "Resume Inception" });
    expect(resume.getAttribute("href")).toBe("/watch/movie/inception");
    expect(within(continueRow).getByText(/ left$/u)).toBeTruthy();

    const recent = screen.getByRole("region", { name: "Recently added" });
    expect(within(recent).getByRole("link", { name: "Breaking Bad" }).getAttribute("href")).toBe(
      "/series/show",
    );
    const classics = screen.getByRole("region", { name: "Classics" });
    expect(
      within(classics)
        .getByRole("link", { name: /See all/u })
        .getAttribute("href"),
    ).toBe("/collections/classics");
  });

  it("switches to Arabic, right to left", async () => {
    mockApi(SIGNED_IN);
    const { user } = renderApp("/");
    await screen.findByRole("region", { name: "Continue watching" });
    await user.click(screen.getByRole("button", { name: /Change language/u }));
    expect(await screen.findByRole("region", { name: "تابع المشاهدة" })).toBeTruthy();
    expect(document.documentElement.dir).toBe("rtl");
    expect(document.documentElement.lang).toBe("ar");
  });

  it("is dark by default, and the theme toggle is remembered", async () => {
    mockApi(SIGNED_IN);
    const { user } = renderApp("/");
    await screen.findByRole("region", { name: "Continue watching" });
    expect(document.documentElement.dataset.theme).toBe("dark");
    await user.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem("smart-iptv.theme")).toBe("light");
  });

  it("sends the customer to sign in when the session ends mid-visit", async () => {
    let expired = false;
    mockApi({
      ...SIGNED_IN,
      "GET /api/v1/favorites": () => (expired ? problem(401, "NOT_AUTHENTICATED") : { body: [] }),
    });
    const { user, router } = renderApp("/");
    await screen.findByRole("region", { name: "Continue watching" });
    expired = true;
    await user.click(first(screen.getAllByRole("link", { name: "My List" })));
    expect(await screen.findByText("Your session ended. Sign in again to continue.")).toBeTruthy();
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/login");
    });
    expect(router.state.location.search).toMatchObject({ redirect: "/my-list", expired: true });
  });

  it("warns on every page when the subscription has ended", async () => {
    mockApi({
      ...SIGNED_IN,
      "GET /api/v1/me": { body: { ...ME, access: { ...ACCESS, status: "expired" } } },
    });
    renderApp("/");
    expect(
      await screen.findByText("Your subscription has ended. Renew to keep watching."),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Renew" }).getAttribute("href")).toBe("/plans");
  });
});
