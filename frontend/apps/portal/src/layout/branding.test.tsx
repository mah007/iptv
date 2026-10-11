import { screen, waitFor } from "@testing-library/react";
import { createMemoryHistory } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App, createAppDependencies } from "../app";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

/* The admin's branding in the portal (ADR-0019): name, logo, support contacts, and the
 * language of a first visit. */

const BRANDING = {
  service_name: { en: "Nile TV", ar: "قناة النيل" },
  accent_color: "#0F766E",
  logo_light_url: "https://media.example.com/images/branding/logo-light-0123456789ab.png",
  logo_dark_url: "https://media.example.com/images/branding/logo-dark-0123456789ab.png",
  support: { email: "help@example.com", phone: "+966501234567", whatsapp: "+966501234567" },
  default_language: "ar",
};

describe("branding", () => {
  it("shows the admin's name, logo and support contacts on the sign-in page", async () => {
    mockApi({
      "GET /api/v1/branding": { body: BRANDING },
      "GET /api/v1/me": problem(401, "NOT_AUTHENTICATED"),
    });
    renderApp("/login");
    expect(await screen.findByText("Nile TV")).toBeTruthy();
    const logo = document.querySelector("header img");
    expect(logo?.getAttribute("src")).toBe(BRANDING.logo_dark_url); // the portal is dark by default
    expect(screen.getByText("help@example.com").closest("a")?.getAttribute("href")).toBe(
      "mailto:help@example.com",
    );
    const whatsapp = screen.getByRole("link", { name: "Message support on WhatsApp" });
    expect(whatsapp.getAttribute("href")).toBe("https://wa.me/966501234567");
  });

  it("keeps the built-in name without branding", async () => {
    mockApi({ "GET /api/v1/me": problem(401, "NOT_AUTHENTICATED") });
    renderApp("/login");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.getAllByText("Smart IPTV").length).toBeGreaterThan(0);
    expect(screen.queryByText("Need help?")).toBeNull();
  });

  it("speaks the service's default language on a first visit", async () => {
    mockApi({
      "GET /api/v1/branding": { body: BRANDING },
      "GET /api/v1/me": problem(401, "NOT_AUTHENTICATED"),
    });
    window.localStorage.removeItem("smart-iptv.language");
    const dependencies = createAppDependencies(createMemoryHistory({ initialEntries: ["/login"] }));
    render(<App {...dependencies} />);
    await waitFor(() => {
      expect(dependencies.i18n.language).toBe("ar");
    });
    expect(await screen.findByText("قناة النيل")).toBeTruthy();
  });
});
