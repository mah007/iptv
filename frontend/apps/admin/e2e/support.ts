import { execFileSync } from "node:child_process";
import path from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

/** Environment the Makefile passes in (see `make e2e-admin`). */
export const env = {
  user: required("E2E_ADMIN_USER"),
  password: required("E2E_ADMIN_PASS"),
  /** argv that prints the admin's next TOTP code (`manage.py totp_code`), JSON-encoded. */
  totpCommand: JSON.parse(required("E2E_TOTP_COMMAND")) as string[],
  tvUrl: process.env.TV_URL ?? "http://tv.localhost:8080",
  artifacts:
    process.env.E2E_ARTIFACTS ?? path.resolve(import.meta.dirname, "../../../../dist/admin-e2e"),
};

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Set ${name} (make e2e-admin does).`);
  return value;
}

/** The next TOTP code the server accepts for the end-to-end admin (DEBUG-only command). */
export function totpCode(): string {
  const [command, ...args] = env.totpCommand;
  if (command === undefined) throw new Error("E2E_TOTP_COMMAND is empty.");
  const output = execFileSync(command, [...args, env.user], { encoding: "utf8" });
  const code = /\b(\d{6})\s*$/u.exec(output)?.[1];
  if (code === undefined) throw new Error("totp_code printed no code.");
  return code;
}

export type Language = "en" | "ar";
export type Theme = "light" | "dark";

/** Language and theme before the app starts (its preferences live in localStorage). */
export async function usePreferences(page: Page, language: Language, theme: Theme): Promise<void> {
  await page.addInitScript(
    ([lang, mode]) => {
      window.localStorage.setItem("smart-iptv.language", lang);
      window.localStorage.setItem("smart-iptv.theme", mode);
    },
    [language, theme] as const,
  );
}

/** Wait until the page shows content: no skeletons or busy regions left in the main area. */
export async function settled(page: Page): Promise<void> {
  // The page itself first (routes load lazily), then its data.
  await expect(page.locator("main h1").first()).toBeVisible();
  await expect(page.locator("main [data-slot='skeleton'], main [aria-busy='true']")).toHaveCount(
    0,
    {
      timeout: 20_000,
    },
  );
}

/** axe-core with the WCAG 2.2 A/AA rules; serious and critical findings fail the test. */
export async function expectAccessible(page: Page, label: string): Promise<void> {
  // Contrast is checked at rest, not under a hover left by the last click.
  await page.mouse.move(0, 0);
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    // Recharts draws decorative SVG inside an aria-hidden figure with a table twin.
    .exclude("[data-slot='chart'] [aria-hidden='true']")
    // Previews of mail and invoices are sandboxed documents without scripts: axe can't
    // run inside them (it would wait for them until the test times out).
    .exclude("iframe[sandbox]")
    .options({ iframes: false })
    .analyze();
  const blocking = results.violations.filter(
    (violation) => violation.impact === "serious" || violation.impact === "critical",
  );
  const report = blocking.map((violation) => ({
    rule: violation.id,
    impact: violation.impact,
    help: violation.help,
    targets: violation.nodes.slice(0, 5).map((node) => node.target.join(" ")),
  }));
  expect(report, `${label}: serious or critical accessibility violations`).toEqual([]);
}
