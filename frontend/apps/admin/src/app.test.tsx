import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { me, signedIn } from "./test/fixtures";
import { mockApi, problem } from "./test/mock-api";
import { renderApp } from "./test/render-app";

const SIGNED_OUT = { "GET /api/v1/auth/me": problem(401, "NOT_AUTHENTICATED") };

describe("admin shell", () => {
  it("shows the dashboard to a signed-in admin and switches to Arabic right-to-left", async () => {
    mockApi(signedIn());
    const { user } = renderApp();
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeTruthy();
    // The active-customers tile (KPIS.customers_active).
    expect(await screen.findByText("19")).toBeTruthy();
    expect(document.title).toBe("Dashboard · Smart IPTV Admin");

    await user.click(screen.getByRole("button", { name: /Change language/ }));

    expect(await screen.findByRole("heading", { name: "لوحة المعلومات" })).toBeTruthy();
    expect(document.documentElement.dir).toBe("rtl");
    expect(document.documentElement.lang).toBe("ar");
  });

  it("lists only the pages the admin's permissions allow, marking the current one", async () => {
    mockApi(signedIn({}, me({ permissions: ["dashboard.view", "customers.view"] })));
    renderApp();
    const nav = await screen.findByRole("navigation", { name: "Main navigation" });
    await waitFor(() => {
      expect(
        within(nav)
          .getAllByRole("link")
          .filter((link) => link.getAttribute("data-slot") === "sidebar-item")
          .map((link) => link.textContent),
      ).toEqual(["Dashboard", "Live sessions", "Customers", "Access rules"]);
    });
    expect(within(nav).getByRole("link", { name: "Dashboard" }).getAttribute("aria-current")).toBe(
      "page",
    );
  });

  it("shows a permission-denied state on a page the admin's roles don't cover", async () => {
    mockApi(signedIn({}, me({ permissions: ["dashboard.view"] })));
    renderApp("/settings");
    expect(await screen.findByText("You don't have access to this")).toBeTruthy();
  });

  it("signs out from the account menu and forgets the session", async () => {
    const api = mockApi(signedIn({ "POST /api/v1/auth/logout": { status: 204 } }));
    const { user, queryClient } = renderApp();
    await user.click(await screen.findByRole("button", { name: "Account menu" }));
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in to Smart IPTV" })).toBeTruthy();
    expect(api.sent("POST", "/api/v1/auth/logout")).toHaveLength(1);
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
  });

  it("shows a 404 page for unknown paths, with a way home", async () => {
    mockApi(signedIn());
    const { user, router } = renderApp("/no-such-page");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "Main navigation" })).toBeNull();

    await user.click(screen.getByRole("link", { name: "Back to home" }));
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeTruthy();
    expect(router.state.location.pathname).toBe("/");
  });
});

describe("signing in", () => {
  it("sends a signed-out visitor to sign in, remembering where they were going", async () => {
    mockApi(SIGNED_OUT);
    const { router } = renderApp("/customers?access=expired");
    expect(await screen.findByRole("heading", { name: "Sign in to Smart IPTV" })).toBeTruthy();
    expect(router.state.location.search).toEqual({ redirect: "/customers?access=expired" });
  });

  it("enrols an authenticator on first sign-in, then opens the page asked for", async () => {
    const api = mockApi({
      ...signedIn(),
      ...SIGNED_OUT,
      "POST /api/v1/auth/login": {
        body: {
          status: "mfa_setup_required",
          otpauth_uri:
            "otpauth://totp/Smart%20IPTV:admin?secret=jbswy3dpehpk3pxp&issuer=Smart%20IPTV",
        },
      },
      "POST /api/v1/auth/mfa/verify": { body: me() },
    });
    const { user, router } = renderApp("/customers");

    await user.type(await screen.findByLabelText("Username or email"), "admin");
    await user.type(screen.getByLabelText("Password"), "correct horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(
      await screen.findByRole("heading", { name: "Set up two-factor authentication" }),
    ).toBeTruthy();
    expect(screen.getByRole("img", { name: /QR code/ })).toBeTruthy();
    expect(screen.getByDisplayValue("JBSWY3DPEHPK3PXP")).toBeTruthy();
    // The enrolment secret never reaches the URL.
    expect(router.state.location.href).not.toContain("otpauth");

    await user.type(screen.getByLabelText("Authentication code"), "123 456");
    await user.click(screen.getByRole("button", { name: "Verify and finish" }));

    expect(await screen.findByRole("heading", { name: "Customers" })).toBeTruthy();
    expect(router.state.location.pathname).toBe("/customers");
    expect(api.sent("POST", "/api/v1/auth/login")[0]?.body).toEqual({
      login: "admin",
      password: "correct horse",
    });
    expect(api.sent("POST", "/api/v1/auth/mfa/verify")[0]?.body).toEqual({ code: "123456" });
  });

  it("asks for the code of an enrolled authenticator and reports a wrong one", async () => {
    mockApi({
      ...SIGNED_OUT,
      "POST /api/v1/auth/login": { body: { status: "mfa_required" } },
      "POST /api/v1/auth/mfa/verify": problem(400, "MFA_INVALID"),
    });
    const { user } = renderApp("/login");
    await user.type(await screen.findByLabelText("Username or email"), "admin");
    await user.type(screen.getByLabelText("Password"), "correct horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Two-factor authentication" })).toBeTruthy();
    await user.type(screen.getByLabelText("Authentication code"), "000000");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(
      await screen.findByText("That code didn't work. Check your app and enter the current code."),
    ).toBeTruthy();
  });

  it("says when the username or password is wrong, without a hint which", async () => {
    mockApi({ ...SIGNED_OUT, "POST /api/v1/auth/login": problem(400, "INVALID_CREDENTIALS") });
    const { user } = renderApp("/login");
    await user.type(await screen.findByLabelText("Username or email"), "admin");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("That username, email or password isn't right.")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Two-factor authentication" })).toBeNull();
  });

  it("validates the sign-in form before sending anything", async () => {
    const api = mockApi(SIGNED_OUT);
    const { user } = renderApp("/login");
    await screen.findByRole("heading", { name: "Sign in to Smart IPTV" });
    expect(document.title).toBe("Sign in to Smart IPTV · Smart IPTV Admin");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter your username or email.")).toBeTruthy();
    expect(screen.getByText("Enter your password.")).toBeTruthy();
    expect(api.sent("POST", "/api/v1/auth/login")).toHaveLength(0);
  });

  it("returns to sign-in when the session ends while working", async () => {
    mockApi({
      "GET /api/v1/auth/me": { body: me() },
      "GET /api/v1/admin/customers": problem(401, "NOT_AUTHENTICATED"),
    });
    const { router } = renderApp("/customers");
    expect(await screen.findByRole("heading", { name: "Sign in to Smart IPTV" })).toBeTruthy();
    expect(router.state.location.search).toEqual({ redirect: "/customers" });
  });

  it("offers a way back from the two-factor step", async () => {
    mockApi(SIGNED_OUT);
    const { user } = renderApp("/login/mfa");
    expect(await screen.findByRole("heading", { name: "Two-factor authentication" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Back to sign in" }));
    expect(await screen.findByRole("heading", { name: "Sign in to Smart IPTV" })).toBeTruthy();
  });
});
