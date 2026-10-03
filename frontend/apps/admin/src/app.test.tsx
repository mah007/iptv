import { createMemoryHistory } from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App, createAppDependencies } from "./app";

function renderApp(path = "/") {
  window.localStorage.setItem("smart-iptv.language", "en");
  const dependencies = createAppDependencies(createMemoryHistory({ initialEntries: [path] }));
  render(<App {...dependencies} />);
  return { ...dependencies, user: userEvent.setup() };
}

describe("admin shell", () => {
  it("renders the home page inside the shell and switches to Arabic right-to-left", async () => {
    const { user } = renderApp();
    expect(await screen.findByRole("heading", { name: "Welcome to Smart IPTV" })).toBeTruthy();
    expect(document.title).toBe("Smart IPTV Admin");

    await user.click(screen.getByRole("button", { name: /Change language/ }));

    expect(await screen.findByRole("heading", { name: "مرحبًا بك في Smart IPTV" })).toBeTruthy();
    expect(document.documentElement.dir).toBe("rtl");
    expect(document.documentElement.lang).toBe("ar");
    await waitFor(() => {
      expect(document.title).toBe("لوحة إدارة Smart IPTV");
    });
  });

  it("lists only pages that exist, marking the current one", async () => {
    renderApp();
    const nav = await screen.findByRole("navigation", { name: "Main navigation" });
    const links = within(nav).getAllByRole("link");
    const pageLinks = links.filter((link) => link.getAttribute("data-slot") === "sidebar-item");
    expect(pageLinks.map((link) => link.textContent)).toEqual(["Home"]);
    expect(pageLinks[0]?.getAttribute("aria-current")).toBe("page");
    expect(within(nav).getByRole("group", { name: "Overview" })).toBeTruthy();
  });

  it("has the environment badge and the display toggles in the topbar", async () => {
    const { user } = renderApp();
    const topbar = (
      await screen.findByRole("heading", { name: "Welcome to Smart IPTV" })
    ).ownerDocument.querySelector("[data-slot=topbar]");
    expect(topbar).toBeTruthy();
    const bar = within(topbar as HTMLElement);
    expect(bar.getByText("DEV")).toBeTruthy();

    await user.click(bar.getByRole("button", { name: "Switch to compact density" }));
    expect(document.documentElement.dataset.density).toBe("compact");

    const themeButton = bar.getByRole("button", { name: /Switch to (dark|light) theme/ });
    const before = document.documentElement.dataset.theme;
    await user.click(themeButton);
    expect(document.documentElement.dataset.theme).not.toBe(before);
  });

  it("collapses the sidebar to icons and remembers it", async () => {
    const { user } = renderApp();
    await user.click(await screen.findByRole("button", { name: "Collapse sidebar" }));
    expect(document.querySelector("[data-slot=sidebar]")?.getAttribute("data-collapsed")).toBe(
      "true",
    );
    expect(window.localStorage.getItem("smart-iptv.sidebar")).toBe("collapsed");
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeTruthy();
  });

  it("signs out to the sign-in screen from the account menu", async () => {
    const { user } = renderApp();
    await user.click(await screen.findByRole("button", { name: "Account menu" }));
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in to Smart IPTV" })).toBeTruthy();
  });

  it("shows a 404 page for unknown paths, with a way home", async () => {
    const { user, router } = renderApp("/no-such-page");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeTruthy();
    expect(document.title).toBe("Page not found · Smart IPTV Admin");
    expect(screen.queryByRole("navigation", { name: "Main navigation" })).toBeNull();

    await user.click(screen.getByRole("link", { name: "Back to home" }));
    expect(await screen.findByRole("heading", { name: "Welcome to Smart IPTV" })).toBeTruthy();
    expect(router.state.location.pathname).toBe("/");
  });
});

describe("sign-in screens", () => {
  it("validates the sign-in form before submitting", async () => {
    const { user } = renderApp("/login");
    await screen.findByRole("heading", { name: "Sign in to Smart IPTV" });
    expect(document.title).toBe("Sign in to Smart IPTV · Smart IPTV Admin");

    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter your username or email.")).toBeTruthy();
    expect(screen.getByText("Enter your password.")).toBeTruthy();
  });

  it("reports sign-in as unavailable until the API is connected", async () => {
    const { user } = renderApp("/login");
    await user.type(await screen.findByLabelText("Username or email"), "admin");
    await user.type(screen.getByLabelText("Password"), "correct horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(
      await screen.findByText("Sign-in isn't available right now. Try again in a few minutes."),
    ).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Welcome to Smart IPTV" })).toBeNull();
  });

  it("offers a way back from the two-factor step", async () => {
    const { user } = renderApp("/login/mfa");
    expect(await screen.findByRole("heading", { name: "Two-factor authentication" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Back to sign in" }));
    expect(await screen.findByRole("heading", { name: "Sign in to Smart IPTV" })).toBeTruthy();
  });
});
