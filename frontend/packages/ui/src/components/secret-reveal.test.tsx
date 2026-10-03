import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithUi } from "../test-utils";
import { SecretReveal } from "./secret-reveal";

const SECRET = "k7Qm-2xPz-9wRt";

describe("SecretReveal", () => {
  it("starts masked without the plaintext in the page", () => {
    const { container } = renderWithUi(<SecretReveal secret={SECRET} label="Password" />);
    expect(container.textContent).not.toContain(SECRET);
    expect(screen.getByText("Hidden")).toBeTruthy();
    expect(screen.getByText("Shown only once. Copy it before you leave this page.")).toBeTruthy();
  });

  it("reveals once, then stays masked for good after hiding", async () => {
    const user = userEvent.setup();
    const onHidden = vi.fn();
    const { container } = renderWithUi(
      <SecretReveal secret={SECRET} label="Password" onHidden={onHidden} />,
    );

    await user.click(screen.getByRole("button", { name: "Reveal" }));
    expect(screen.getByText(SECRET)).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Hide" }));
    expect(container.textContent).not.toContain(SECRET);
    expect(onHidden).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Reveal" })).toBeNull();
    expect(screen.getByRole("button", { name: "Copy" })).toHaveProperty("disabled", true);
    expect(screen.getByText("Hidden for good. It can't be shown again.")).toBeTruthy();
  });

  it("copies the secret without revealing it", async () => {
    const user = userEvent.setup();
    const { container } = renderWithUi(<SecretReveal secret={SECRET} />);
    await user.click(screen.getByRole("button", { name: "Copy" }));
    await expect(navigator.clipboard.readText()).resolves.toBe(SECRET);
    expect(container.textContent).not.toContain(SECRET);
    expect(await screen.findByRole("button", { name: "Copied to clipboard" })).toBeTruthy();
  });
});
