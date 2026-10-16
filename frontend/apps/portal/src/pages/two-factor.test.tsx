import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { normaliseCode, otpauthSecret } from "../features/account/two-factor";
import { HOME, ME } from "../test/fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

/* Optional two-factor sign-in for customers (ADR-0024). */

const SIGNED_IN = {
  "GET /api/v1/me": { body: ME },
  "GET /api/v1/me/subscription": { body: { subscription: null, pending: null } },
  "GET /api/v1/home": { body: HOME },
};
const URI = "otpauth://totp/Smart%20IPTV:layla?secret=JBSWY3DPEHPK3PXP&issuer=Smart%20IPTV";

describe("two-factor sign-in", () => {
  it("asks for the code after the right password, then signs in", async () => {
    let signedIn = false;
    const api = mockApi({
      ...SIGNED_IN,
      "GET /api/v1/me": () => (signedIn ? { body: ME } : problem(401, "NOT_AUTHENTICATED")),
      "GET /api/v1/auth/csrf": { status: 204 },
      "POST /api/v1/auth/login": (request) => {
        const code = (request.body as { code?: string }).code;
        if (code === undefined) return problem(403, "MFA_REQUIRED");
        if (code !== "123456") return problem(400, "MFA_INVALID");
        signedIn = true;
        return { body: { ...ME, mfa_enabled: true } };
      },
    });
    const { user } = renderApp("/login");
    await user.type(await screen.findByLabelText("Username, email or phone"), "layla");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Enter your code" })).toBeTruthy();
    const code = screen.getByLabelText("Authentication code");
    expect(code.getAttribute("autocomplete")).toBe("one-time-code");
    await user.type(code, "12");
    await user.click(screen.getByRole("button", { name: "Verify and sign in" }));
    expect(
      await screen.findByText("Enter the six digits from your authenticator app."),
    ).toBeTruthy();
    await user.clear(code);
    await user.type(code, "999 999");
    await user.click(screen.getByRole("button", { name: "Verify and sign in" }));
    expect(await screen.findByText(/That code didn't work/u)).toBeTruthy();
    await user.type(screen.getByLabelText("Authentication code"), "123 456");
    await user.click(screen.getByRole("button", { name: "Verify and sign in" }));

    expect(await screen.findByRole("heading", { name: "Continue watching" })).toBeTruthy();
    expect(api.sent("POST", "/api/v1/auth/login").at(-1)?.body).toEqual({
      login: "layla",
      password: "correct horse battery",
      code: "123456",
    });
  });

  it("goes back to the password with Back", async () => {
    mockApi({
      "GET /api/v1/me": problem(401, "NOT_AUTHENTICATED"),
      "GET /api/v1/auth/csrf": { status: 204 },
      "POST /api/v1/auth/login": problem(403, "MFA_REQUIRED"),
    });
    const { user } = renderApp("/login");
    await user.type(await screen.findByLabelText("Username, email or phone"), "layla");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await user.click(await screen.findByRole("button", { name: "Back" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.getByLabelText<HTMLInputElement>("Password").value).toBe("");
  });

  it("sets it up from the account page with the app's first code", async () => {
    const api = mockApi({
      ...SIGNED_IN,
      "POST /api/v1/me/mfa": { body: { otpauth_uri: URI } },
      "POST /api/v1/me/mfa/confirm": (request) =>
        (request.body as { code: string }).code === "654321"
          ? { body: { ...ME, mfa_enabled: true } }
          : problem(400, "MFA_INVALID"),
    });
    const { user } = renderApp("/account");
    const card = (await screen.findByRole("heading", { name: /Two-factor sign-in/u })).closest(
      "[data-slot='card']",
    );
    if (!(card instanceof HTMLElement)) throw new Error("No two-factor card.");
    expect(within(card).getByText("Off")).toBeTruthy();
    await user.click(within(card).getByRole("button", { name: "Set up two-factor sign-in" }));
    const dialog = await screen.findByRole("dialog", { name: "Set up two-factor sign-in" });
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    expect(
      await within(dialog).findByRole("img", { name: "QR code for your authenticator app" }),
    ).toBeTruthy();
    expect(within(dialog).getByDisplayValue("JBSWY3DPEHPK3PXP")).toBeTruthy();
    await user.type(within(dialog).getByLabelText("Code from the app"), "111111");
    await user.click(within(dialog).getByRole("button", { name: "Turn on" }));
    expect(await within(dialog).findByText(/That code didn't work/u)).toBeTruthy();
    await user.clear(within(dialog).getByLabelText("Code from the app"));
    await user.type(within(dialog).getByLabelText("Code from the app"), "654 321");
    await user.click(within(dialog).getByRole("button", { name: "Turn on" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Set up two-factor sign-in" })).toBeNull();
    });
    expect(await within(card).findByText("On")).toBeTruthy();
    expect(api.sent("POST", "/api/v1/me/mfa/confirm").at(-1)?.body).toEqual({ code: "654321" });
  });

  it("turns it off after a confirmation", async () => {
    const api = mockApi({
      ...SIGNED_IN,
      "GET /api/v1/me": { body: { ...ME, mfa_enabled: true } },
      "DELETE /api/v1/me/mfa": { status: 204 },
    });
    const { user } = renderApp("/account");
    await user.click(await screen.findByRole("button", { name: "Turn off" }));
    const confirm = await screen.findByRole("alertdialog", {
      name: "Turn off two-factor sign-in?",
    });
    await user.click(within(confirm).getByRole("button", { name: "Turn off" }));
    expect((await screen.findAllByText("Two-factor sign-in is off.")).length).toBeGreaterThan(0);
    expect(api.sent("DELETE", "/api/v1/me/mfa")).toHaveLength(1);
  });

  it("reads the key from the URI and spaces out of codes", () => {
    expect(otpauthSecret(URI)).toBe("JBSWY3DPEHPK3PXP");
    expect(otpauthSecret("not a uri")).toBe("");
    expect(normaliseCode(" 123 456 ")).toBe("123456");
  });
});
