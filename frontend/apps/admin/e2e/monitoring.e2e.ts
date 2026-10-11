import { expect, test } from "@playwright/test";

import { expectAccessible, settled } from "./support";

/*
 * Monitoring (ADR-0018): from the admin's Monitoring page into Grafana, signed in by
 * the admin's own session (one-time ticket, forward-auth), with the provisioned
 * dashboards. Runs only when the stack was started with the monitoring overlay
 * (`make up MONITORING=1`, which `make e2e-admin` passes on as E2E_MONITORING) and
 * grafana.<domain> answers; otherwise it is skipped. Leftover overlay containers from an
 * earlier run don't count: web only offers Grafana when the overlay is part of the stack.
 */

const ADMIN_URL = process.env.ADMIN_URL ?? "http://admin.localhost:8080";
const GRAFANA_URL = process.env.GRAFANA_URL ?? ADMIN_URL.replace("//admin.", "//grafana.");

test.describe("monitoring", () => {
  test.beforeAll(async ({ request }) => {
    test.skip(!process.env.E2E_MONITORING, "the stack was started without the monitoring overlay");
    const reply = await request
      .get(`${GRAFANA_URL}/api/health`, { failOnStatusCode: false, maxRedirects: 0 })
      .catch(() => null);
    // Behind the admin's sign-in, a request without a session gets 401.
    test.skip(reply?.status() !== 401, "the monitoring overlay is not running");
  });

  test("opens Grafana signed in, with the dashboards", async ({ page }) => {
    await page.goto("/monitoring");
    await settled(page);
    await expect(page.getByRole("heading", { level: 1, name: "Monitoring" })).toBeVisible();
    await expect(page.getByText("Set at installation")).toBeVisible();
    await expectAccessible(page, "monitoring");

    await page.getByRole("button", { name: "Open" }).first().click();
    await page.waitForURL(/\/\/grafana\./u);
    // Grafana's home is the Streaming Ops dashboard; its search lists all seven.
    await expect(page.getByText("Streaming Ops").first()).toBeVisible({ timeout: 30_000 });
    const search = await page.request.get(`${GRAFANA_URL}/api/search?tag=smart-iptv`);
    expect(search.status()).toBe(200);
    expect(((await search.json()) as unknown[]).length).toBe(7);

    // Grafana's sign-out ends the monitoring session and goes back to the admin.
    await page.goto(`${GRAFANA_URL}/_sso/logout`);
    await page.waitForURL(/\/\/admin\./u);
    const after = await page.request.get(`${GRAFANA_URL}/api/search`, {
      failOnStatusCode: false,
    });
    expect(after.status()).toBe(401);
  });
});
