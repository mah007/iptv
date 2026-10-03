import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { House, Users } from "lucide-react";
import { describe, expect, it } from "vitest";

import { renderWithUi } from "../test-utils";
import {
  Sidebar,
  SidebarContent,
  SidebarItem,
  SidebarProvider,
  SidebarSection,
  SidebarTrigger,
} from "./sidebar";

function Shell() {
  return (
    <SidebarProvider>
      <Sidebar label="Main navigation">
        <SidebarContent>
          <SidebarSection title="Overview">
            <SidebarItem asChild icon={<House />} label="Home">
              <a href="/" aria-current="page" />
            </SidebarItem>
            <SidebarItem asChild icon={<Users />} label="Customers" badge={4}>
              <a href="/customers" />
            </SidebarItem>
          </SidebarSection>
        </SidebarContent>
      </Sidebar>
      <SidebarTrigger />
    </SidebarProvider>
  );
}

describe("Sidebar", () => {
  it("renders labelled links grouped by section, marking the current page", () => {
    renderWithUi(<Shell />);
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(nav).toBeTruthy();
    expect(screen.getByRole("group", { name: "Overview" })).toBeTruthy();
    const home = screen.getByRole("link", { name: "Home" });
    expect(home.getAttribute("href")).toBe("/");
    expect(home.getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: /Customers/ }).textContent).toContain("4");
  });

  it("collapses to an icon rail, keeps names for screen readers, and remembers it", async () => {
    const user = userEvent.setup();
    const { unmount } = renderWithUi(<Shell />);
    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));

    const aside = document.querySelector("[data-slot=sidebar]");
    expect(aside?.getAttribute("data-collapsed")).toBe("true");
    expect(screen.getByRole("link", { name: "Home" })).toBeTruthy();
    expect(screen.getByText("Home").className).toContain("sr-only");
    expect(window.localStorage.getItem("smart-iptv.sidebar")).toBe("collapsed");

    unmount();
    renderWithUi(<Shell />);
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeTruthy();
  });

  it("opens the navigation as a sheet on small screens", async () => {
    const user = userEvent.setup();
    renderWithUi(<Shell />);
    await user.click(screen.getByRole("button", { name: "Open navigation" }));
    const sheet = await screen.findByRole("dialog", { name: "Main navigation" });
    expect(sheet).toBeTruthy();
  });
});
