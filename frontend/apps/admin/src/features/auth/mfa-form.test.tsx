import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "../../test-utils";
import { MfaForm } from "./mfa-form";
import { otpauthSecret } from "./schemas";

const URI =
  "otpauth://totp/Smart%20IPTV:admin?secret=jbswy3dpehpk3pxp&issuer=Smart%20IPTV&digits=6&period=30";

describe("MfaForm (verify)", () => {
  it("rejects anything but six digits", async () => {
    const onSubmit = vi.fn();
    const { user } = renderWithProviders(
      <MfaForm mode="verify" onSubmit={onSubmit} onBack={vi.fn()} />,
    );
    await user.type(screen.getByLabelText("Authentication code"), "12ab");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("The code is 6 digits, e.g. 123456.")).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("asks for the code when it is empty", async () => {
    const { user } = renderWithProviders(
      <MfaForm mode="verify" onSubmit={vi.fn()} onBack={vi.fn()} />,
    );
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("Enter the 6-digit code.")).toBeTruthy();
  });

  it("accepts a spaced code and submits the digits", async () => {
    const onSubmit = vi.fn();
    const { user } = renderWithProviders(
      <MfaForm mode="verify" onSubmit={onSubmit} onBack={vi.fn()} />,
    );
    await user.type(screen.getByLabelText("Authentication code"), "123 456");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledOnce();
    });
    expect(onSubmit.mock.calls[0]?.[0]).toEqual({ code: "123456" });
  });

  it("shows a wrong-code error and goes back", async () => {
    const onBack = vi.fn();
    const { user } = renderWithProviders(
      <MfaForm mode="verify" onSubmit={vi.fn()} onBack={onBack} errorCode="MFA_INVALID" />,
    );
    expect(screen.getByRole("alert").textContent).toBe(
      "That code didn't work. Check your app and enter the current code.",
    );
    await user.click(screen.getByRole("button", { name: "Back to sign in" }));
    expect(onBack).toHaveBeenCalledOnce();
  });
});

describe("MfaForm (setup)", () => {
  it("shows the enrolment QR code and the manual setup key", () => {
    renderWithProviders(
      <MfaForm mode="setup" otpauthUri={URI} onSubmit={vi.fn()} onBack={vi.fn()} />,
    );
    expect(screen.getByRole("heading", { name: "Set up two-factor authentication" })).toBeTruthy();
    expect(
      screen.getByRole("img", { name: "QR code to add Smart IPTV to an authenticator app" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("textbox", { name: "Can't scan? Enter this setup key instead" }),
    ).toHaveProperty("value", "JBSWY3DPEHPK3PXP");
    expect(screen.getByRole("button", { name: "Verify and finish" })).toBeTruthy();
  });
});

describe("otpauthSecret", () => {
  it("extracts the base32 secret", () => {
    expect(otpauthSecret(URI)).toBe("JBSWY3DPEHPK3PXP");
  });

  it("returns null for malformed URIs", () => {
    expect(otpauthSecret("not a uri")).toBeNull();
    expect(otpauthSecret("otpauth://totp/x?issuer=y")).toBeNull();
  });
});
