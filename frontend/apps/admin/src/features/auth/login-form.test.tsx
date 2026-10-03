import { screen, waitFor } from "@testing-library/react";
import { act } from "react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "../../test-utils";
import { LoginForm } from "./login-form";

describe("LoginForm", () => {
  it("requires both fields and marks them invalid", async () => {
    const onSubmit = vi.fn();
    const { user } = renderWithProviders(<LoginForm onSubmit={onSubmit} />);
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Enter your username or email.")).toBeTruthy();
    expect(screen.getByText("Enter your password.")).toBeTruthy();
    expect(screen.getByLabelText("Username or email").getAttribute("aria-invalid")).toBe("true");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("submits trimmed credentials", async () => {
    const onSubmit = vi.fn();
    const { user } = renderWithProviders(<LoginForm onSubmit={onSubmit} />);
    await user.type(screen.getByLabelText("Username or email"), "  admin@example.com ");
    await user.type(screen.getByLabelText("Password"), "s3cret pass");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledOnce();
    });
    expect(onSubmit.mock.calls[0]?.[0]).toEqual({
      login: "admin@example.com",
      password: "s3cret pass",
    });
  });

  it("keeps the button busy while the submission runs", async () => {
    let finish: () => void = () => undefined;
    const onSubmit = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    const { user } = renderWithProviders(<LoginForm onSubmit={onSubmit} />);
    await user.type(screen.getByLabelText("Username or email"), "admin");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const busy = await screen.findByRole("button", { name: "Signing in…" });
    expect(busy).toHaveProperty("disabled", true);
    act(() => {
      finish();
    });
    expect(await screen.findByRole("button", { name: "Sign in" })).toHaveProperty(
      "disabled",
      false,
    );
  });

  it.each([
    ["INVALID_CREDENTIALS", "That username, email or password isn't right."],
    [
      "ACCOUNT_LOCKED",
      "Too many failed attempts. This account is locked for a while; try again later.",
    ],
    ["INTERNAL_ERROR", "Sign-in isn't available right now. Try again in a few minutes."],
  ])("explains the %s error", (code, message) => {
    renderWithProviders(<LoginForm onSubmit={vi.fn()} errorCode={code} />);
    expect(screen.getByRole("alert").textContent).toBe(message);
  });

  it("can show the password while typing it", async () => {
    const { user } = renderWithProviders(<LoginForm onSubmit={vi.fn()} />);
    const password = screen.getByLabelText("Password");
    expect(password.getAttribute("type")).toBe("password");
    await user.click(screen.getByRole("button", { name: "Show password" }));
    expect(password.getAttribute("type")).toBe("text");
  });

  it("re-translates visible errors when the language changes", async () => {
    const { user, i18n } = renderWithProviders(<LoginForm onSubmit={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByText("Enter your password.");
    await act(async () => {
      await i18n.changeLanguage("ar");
    });
    expect(screen.getByText("أدخل كلمة المرور.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "تسجيل الدخول" })).toBeTruthy();
  });
});
