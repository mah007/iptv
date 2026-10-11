import type { MonitoringStatus } from "@smart-iptv/api";
import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { safeMonitoringPath } from "../features/monitoring/search";
import { ALL_PERMISSIONS, me, signedIn } from "../test/fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

/* The Monitoring page (ADR-0018): one-time links into Grafana, Prometheus and
 * Alertmanager, alert delivery, the test alert and the values set at installation. */

const STATUS: MonitoringStatus = {
  enabled: true,
  grafana_url: "https://grafana.tv.example.com",
  alerts_enabled: true,
  email_recipients: 2,
  email_configured: true,
  telegram_configured: false,
  fire_drill_active: false,
  deployment: {
    prometheus_retention: "30d",
    prometheus_retention_size: "10GB",
    loki_retention: "720h",
    fallback_email: true,
    fallback_telegram: false,
  },
};
const LINK = "https://grafana.tv.example.com/_sso/callback?ticket=abc";
const WATCHER = me({ roles: ["viewer"], permissions: ["monitoring.view"] });
const OWNER = me({ permissions: [...ALL_PERMISSIONS, "monitoring.view"] });

let assign: ReturnType<typeof vi.fn>;

beforeEach(() => {
  assign = vi.fn();
  vi.stubGlobal("location", { href: window.location.href, origin: window.location.origin, assign });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("monitoring", () => {
  it("opens each tool with a one-time link", async () => {
    const api = mockApi(
      signedIn(
        {
          "GET /api/v1/admin/monitoring": { body: STATUS },
          "POST /api/v1/admin/monitoring/ticket": { body: { url: LINK } },
        },
        WATCHER,
      ),
    );
    const { user } = renderApp("/monitoring");
    expect(await screen.findByRole("heading", { level: 1, name: "Monitoring" })).toBeTruthy();
    expect(await screen.findByText("2 recipients")).toBeTruthy();
    expect(screen.getByText("30d, at most 10GB")).toBeTruthy();
    // Viewers see the delivery but can't send a test alert or edit the settings.
    expect(screen.queryByRole("button", { name: "Send a test alert" })).toBeNull();
    const [, prometheus, ...others] = screen.getAllByRole("button", { name: "Open" });
    expect(others).toHaveLength(1);
    if (prometheus === undefined) throw new Error("no Prometheus button");
    await user.click(prometheus);
    await waitFor(() => {
      expect(assign).toHaveBeenCalledWith(LINK);
    });
    expect(api.sent("POST", "/api/v1/admin/monitoring/ticket")[0]?.body).toEqual({
      next: "/prometheus/",
    });
  });

  it("carries on to the page monitoring asked for", async () => {
    const api = mockApi(
      signedIn(
        {
          "GET /api/v1/admin/monitoring": { body: STATUS },
          "POST /api/v1/admin/monitoring/ticket": { body: { url: LINK } },
        },
        WATCHER,
      ),
    );
    renderApp("/monitoring?next=%2Fd%2Fiptv-edges");
    await waitFor(() => {
      expect(assign).toHaveBeenCalledWith(LINK);
    });
    expect(api.sent("POST", "/api/v1/admin/monitoring/ticket")[0]?.body).toEqual({
      next: "/d/iptv-edges",
    });
  });

  it("explains a failed sign-in link instead of retrying it", async () => {
    const api = mockApi(signedIn({ "GET /api/v1/admin/monitoring": { body: STATUS } }, WATCHER));
    renderApp("/monitoring?next=%2F&failed=1");
    expect(await screen.findByText("That sign-in link no longer works")).toBeTruthy();
    expect(api.sent("POST", "/api/v1/admin/monitoring/ticket")).toHaveLength(0);
  });

  it("sends a test alert", async () => {
    const api = mockApi(
      signedIn(
        {
          "GET /api/v1/admin/monitoring": { body: STATUS },
          "POST /api/v1/admin/monitoring/test-alert": {
            body: { active: true, until: new Date().toISOString() },
          },
        },
        OWNER,
      ),
    );
    const { user } = renderApp("/monitoring");
    await user.click(await screen.findByRole("button", { name: "Send a test alert" }));
    await waitFor(() => {
      expect(api.sent("POST", "/api/v1/admin/monitoring/test-alert")).toHaveLength(1);
    });
    expect(api.sent("POST", "/api/v1/admin/monitoring/test-alert")[0]?.body).toEqual({
      minutes: 10,
    });
    expect(screen.getByRole("link", { name: "Change in Settings" })).toBeTruthy();
  });

  it("says when monitoring isn't turned on", async () => {
    mockApi(
      signedIn(
        {
          "GET /api/v1/admin/monitoring": { body: { ...STATUS, enabled: false, grafana_url: "" } },
          "POST /api/v1/admin/monitoring/ticket": problem(404, "NOT_FOUND"),
        },
        WATCHER,
      ),
    );
    renderApp("/monitoring");
    expect(await screen.findByText("Monitoring isn't turned on")).toBeTruthy();
  });

  it("is refused without the permission", async () => {
    mockApi(signedIn({}, me({ roles: ["support"], permissions: ["customers.view"] })));
    renderApp("/monitoring");
    expect(await screen.findByText("You don't have access to this")).toBeTruthy();
  });
});

describe("monitoring landing paths", () => {
  it.each([
    ["/d/iptv-edges", "/d/iptv-edges"],
    ["/prometheus/alerts", "/prometheus/alerts"],
    ["https://evil.example/", undefined],
    ["//evil.example/", undefined],
    ["/\\evil", undefined],
    ["relative", undefined],
    [42, undefined],
  ])("%s", (raw, expected) => {
    expect(safeMonitoringPath(raw)).toBe(expected);
  });
});
