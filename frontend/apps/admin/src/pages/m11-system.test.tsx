import type { AccessRule, Health } from "@smart-iptv/api";
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { page, signedIn } from "../test/fixtures";
import { mockApi } from "../test/mock-api";
import { renderApp } from "../test/render-app";

/* System health, access rules and the command palette (SPEC §8.2, §8.3). */

const HEALTH: Health = {
  as_of: new Date().toISOString(),
  status: "degraded",
  services: [
    { name: "postgres", status: "ok", latency_ms: 1.2, age_s: null, error: "" },
    { name: "meilisearch", status: "down", latency_ms: null, age_s: null, error: "ConnectError" },
  ],
  queues: [{ name: "transcode.cpu", depth: 4 }],
  transcoders: [],
  redis: [
    {
      name: "redis_state",
      status: "ok",
      used_memory: 10_000_000,
      max_memory: 100_000_000,
      policy: "noeviction",
      evicted_keys: 0,
      error: "",
    },
  ],
  database: { status: "ok", connections: 12, max_connections: 100, error: "" },
  grafana_url: "",
};

function rule(overrides: Partial<AccessRule> = {}): AccessRule {
  return {
    id: "rule-1",
    user: null,
    type: "country_deny",
    value: "XX",
    reason: "Not licensed there",
    expires_at: null,
    created_at: "2026-09-01T10:00:00Z",
    ...overrides,
  };
}

describe("system health", () => {
  it("shows each service's state and the queue depths", async () => {
    mockApi(signedIn({ "GET /api/v1/admin/health": { body: HEALTH } }));
    renderApp("/health");
    expect(await screen.findByRole("heading", { level: 1, name: /System health/u })).toBeTruthy();
    expect((await screen.findAllByText("Meilisearch")).length).toBeGreaterThan(0);
    expect(document.body.textContent).toContain("ConnectError");
    expect(document.body.textContent).toContain("transcode.cpu");
  });
});

describe("access rules", () => {
  it("lists the rules for everyone and adds one", async () => {
    const api = mockApi(
      signedIn({
        "GET /api/v1/admin/access-rules": { body: page([rule()]) },
        "POST /api/v1/admin/access-rules": {
          status: 201,
          body: rule({ id: "rule-2", type: "ip_deny", value: "203.0.113.9" }),
        },
      }),
    );
    const { user } = renderApp("/access-rules");
    expect(await screen.findByText("Not licensed there")).toBeTruthy();
    expect(screen.getAllByText("Block country").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Add rule" }));
    const dialog = await screen.findByRole("dialog", { name: "New rule for everyone" });
    await user.type(within(dialog).getByLabelText("Value"), "203.0.113.9");
    await user.click(within(dialog).getByRole("button", { name: "Add rule" }));
    await waitFor(() => {
      expect(api.sent("POST", "/api/v1/admin/access-rules")).toHaveLength(1);
    });
    expect(api.sent("POST", "/api/v1/admin/access-rules")[0]?.body).toMatchObject({
      user: null,
      type: "ip_deny",
      value: "203.0.113.9",
    });
  });
});

describe("command palette", () => {
  it("opens with Ctrl+K and jumps to a page", async () => {
    mockApi(signedIn({ "GET /api/v1/admin/settings": { body: [] } }));
    const { user } = renderApp("/");
    expect(await screen.findByRole("heading", { level: 1 })).toBeTruthy();
    await user.keyboard("{Control>}k{/Control}");
    const palette = await screen.findByRole("dialog", { name: "Command palette" });
    await user.type(within(palette).getByRole("combobox"), "Settings");
    await user.click(await within(palette).findByRole("option", { name: /Settings/u }));
    expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeTruthy();
  });

  it("lists the keyboard shortcuts on ?", async () => {
    mockApi(signedIn());
    const { user } = renderApp("/");
    expect(await screen.findByRole("heading", { level: 1 })).toBeTruthy();
    await user.keyboard("?");
    expect(await screen.findByRole("dialog", { name: "Keyboard shortcuts" })).toBeTruthy();
  });
});
