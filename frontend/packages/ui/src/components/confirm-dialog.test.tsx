import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithUi } from "../test-utils";
import { Button } from "./button";
import { ConfirmDialog } from "./confirm-dialog";

function renderDialog(onConfirm: () => void | Promise<void>, confirmationText?: string) {
  const view = renderWithUi(
    <ConfirmDialog
      title="Suspend customer?"
      description="They lose access to every stream immediately."
      confirmLabel="Suspend"
      tone="danger"
      onConfirm={onConfirm}
      trigger={<Button>Open</Button>}
      {...(confirmationText === undefined ? {} : { confirmationText })}
    />,
  );
  return { ...view, user: userEvent.setup() };
}

describe("ConfirmDialog", () => {
  it("keeps the action disabled until the exact text is typed", async () => {
    const onConfirm = vi.fn();
    const { user } = renderDialog(onConfirm, "mah-7k3p9q");
    await user.click(screen.getByRole("button", { name: "Open" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Suspend customer?" });
    expect(dialog).toBeTruthy();
    const confirm = screen.getByRole("button", { name: "Suspend" });
    expect(confirm).toHaveProperty("disabled", true);

    const input = screen.getByRole("textbox", { name: /Type mah-7k3p9q to confirm/ });
    await user.type(input, "mah-7k3p9");
    expect(confirm).toHaveProperty("disabled", true);
    await user.type(input, "Q");
    expect(confirm).toHaveProperty("disabled", true);
    await user.type(input, "{Backspace}q");
    expect(confirm).toHaveProperty("disabled", false);

    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledOnce();
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
  });

  it("confirms straight away when no typed text is required", async () => {
    const onConfirm = vi.fn();
    const { user } = renderDialog(onConfirm);
    await user.click(screen.getByRole("button", { name: "Open" }));
    await user.click(await screen.findByRole("button", { name: "Suspend" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("stays open when the action fails, so it can be retried", async () => {
    const onConfirm = vi.fn(() => Promise.reject(new Error("network")));
    const { user } = renderDialog(onConfirm);
    await user.click(screen.getByRole("button", { name: "Open" }));
    await user.click(await screen.findByRole("button", { name: "Suspend" }));
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Suspend" })).toHaveProperty("disabled", false);
    });
  });

  it("clears the typed text when cancelled", async () => {
    const { user } = renderDialog(vi.fn(), "DELETE");
    await user.click(screen.getByRole("button", { name: "Open" }));
    await user.type(await screen.findByRole("textbox"), "DELETE");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
    await user.click(screen.getByRole("button", { name: "Open" }));
    expect((await screen.findByRole("textbox")).getAttribute("value")).toBe("");
  });
});
