import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithUi } from "../test-utils";
import { PRINTING_CLASS, PrintSetupCard, SetupCard, setupCardQrValue } from "./setup-card";

const LOGIN = {
  serviceName: "Smart IPTV",
  serverUrl: "https://tv.example.com",
  username: "huda-tv",
  password: "k7Qm-2xPz-9wRt",
  deviceName: "Living room TV",
};

/** jsdom has no print dialog: record the card that was on the page when it opened. */
function stubPrint() {
  const printed: { card: string; printing: boolean }[] = [];
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
    window.setTimeout(() => {
      callback(0);
    }, 0),
  );
  vi.spyOn(window, "print").mockImplementation(() => {
    const root = document.querySelector("[data-print-root]");
    printed.push({
      card: root?.textContent ?? "",
      printing: document.documentElement.classList.contains(PRINTING_CLASS),
    });
  });
  return printed;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("setup cards (G-25)", () => {
  it("holds the password in the QR code only when it is printed", () => {
    expect(JSON.parse(setupCardQrValue(LOGIN, true))).toEqual({
      server: LOGIN.serverUrl,
      username: LOGIN.username,
      password: LOGIN.password,
    });
    expect(JSON.parse(setupCardQrValue(LOGIN, false))).toEqual({
      server: LOGIN.serverUrl,
      username: LOGIN.username,
    });
  });

  it("shows the login, the steps and a warning, in Arabic too", () => {
    const at = new Date("2026-10-16T09:00:00Z");
    const { unmount } = renderWithUi(<SetupCard login={LOGIN} withPassword printedAt={at} />);
    const card = screen.getByRole("article", { name: "TV app setup card" });
    expect(within(card).getByText(LOGIN.password)).toBeTruthy();
    expect(within(card).getByText("For: Living room TV")).toBeTruthy();
    expect(
      within(card).getByRole("img", { name: "QR code with the server, username and password" }),
    ).toBeTruthy();
    expect(within(card).getByText(/anyone who has it can watch/)).toBeTruthy();
    unmount();
    renderWithUi(<SetupCard login={LOGIN} withPassword={false} printedAt={at} />, {
      language: "ar",
    });
    const arabic = screen.getByRole("article", { name: "بطاقة إعداد تطبيق التلفاز" });
    expect(arabic.textContent).not.toContain(LOGIN.password);
    expect(within(arabic).getByText("غير مطبوعة: استخدم الكلمة التي حفظتها")).toBeTruthy();
  });

  it("prints the card alone, then takes it off the page", async () => {
    const printed = stubPrint();
    const user = userEvent.setup();
    renderWithUi(<PrintSetupCard login={LOGIN} />);
    expect(document.querySelector("[data-print-root]")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Print setup card" }));
    await vi.waitFor(() => {
      expect(printed).toHaveLength(1);
    });
    expect(printed[0]?.printing).toBe(true);
    expect(printed[0]?.card).toContain(LOGIN.password);
    act(() => {
      window.dispatchEvent(new Event("afterprint"));
    });
    expect(document.querySelector("[data-print-root]")).toBeNull();
    expect(document.documentElement.classList.contains(PRINTING_CLASS)).toBe(false);
  });

  it("leaves the password off when the box is cleared, and can't print once it's gone", async () => {
    const printed = stubPrint();
    const user = userEvent.setup();
    const { rerender } = renderWithUi(<PrintSetupCard login={LOGIN} />);
    await user.click(screen.getByRole("checkbox", { name: "Include the password" }));
    await user.click(screen.getByRole("button", { name: "Print setup card" }));
    await vi.waitFor(() => {
      expect(printed).toHaveLength(1);
    });
    expect(printed[0]?.card).not.toContain(LOGIN.password);
    act(() => {
      window.dispatchEvent(new Event("afterprint"));
    });
    rerender(<PrintSetupCard login={LOGIN} disabled />);
    expect(screen.getByRole("button", { name: "Print setup card" })).toHaveProperty(
      "disabled",
      true,
    );
  });
});
