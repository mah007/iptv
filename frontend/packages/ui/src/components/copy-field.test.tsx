import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithUi } from "../test-utils";
import { CopyField } from "./copy-field";
import { Toaster } from "./toaster";

describe("CopyField", () => {
  it("copies the value and confirms it", async () => {
    const user = userEvent.setup();
    renderWithUi(
      <>
        <CopyField label="Username" value="mah-7k3p9q" />
        <Toaster />
      </>,
    );
    expect(screen.getByRole("textbox", { name: "Username" })).toHaveProperty("value", "mah-7k3p9q");

    await user.click(screen.getByRole("button", { name: "Copy" }));
    await expect(navigator.clipboard.readText()).resolves.toBe("mah-7k3p9q");
    expect(await screen.findByRole("button", { name: "Copied to clipboard" })).toBeTruthy();
    expect(
      await screen.findByText("Copied to clipboard", { selector: "[data-title]" }),
    ).toBeTruthy();
  });

  it("explains manual copying and selects the text when the clipboard refuses", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValueOnce(new Error("denied"));
    renderWithUi(
      <>
        <CopyField aria-label="Server URL" value="http://tv.example.com" />
        <Toaster />
      </>,
    );
    const input = screen.getByRole<HTMLInputElement>("textbox", { name: "Server URL" });

    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(
      await screen.findByText("Couldn't copy automatically. Select the text and copy it manually."),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe("http://tv.example.com".length);
  });
});
