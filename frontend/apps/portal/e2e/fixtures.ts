import { execFileSync } from "node:child_process";

import AxeBuilder from "@axe-core/playwright";
import { expect, test as base, type Page } from "@playwright/test";

declare global {
  interface Window {
    __cspViolations?: string[];
  }
}

/*
 * Every portal test's page fails on a Content-Security-Policy violation (ADR-0021): the
 * page's `securitypolicyviolation` events and Chrome's console errors.
 */
export const test = base.extend({
  page: async ({ page }, provide) => {
    await page.addInitScript(() => {
      window.__cspViolations = [];
      document.addEventListener("securitypolicyviolation", (event) => {
        window.__cspViolations?.push(
          `${event.effectiveDirective} blocked ${event.blockedURI || "inline"}`,
        );
      });
    });
    const fromConsole: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error" && message.text().includes("Content Security Policy")) {
        fromConsole.push(message.text());
      }
    });

    await provide(page);

    const fromPage = page.isClosed()
      ? []
      : await page.evaluate(() => window.__cspViolations ?? []).catch(() => []);
    expect([...fromPage, ...fromConsole], "Content-Security-Policy violations").toEqual([]);
  },
});

export { expect };

/** The customer `make e2e-portal` creates (manage.py e2e_portal_account). */
export const login = process.env.E2E_PORTAL_USER ?? "";
export const password = process.env.E2E_PORTAL_PASS ?? "";

/**
 * The customer's next authenticator code (two-factor sign-in, ADR-0024), from the
 * DEBUG-only `manage.py totp_code`: `make e2e-portal` passes its argv as E2E_TOTP_COMMAND.
 */
export function totpCode(): string {
  const argv = JSON.parse(process.env.E2E_TOTP_COMMAND ?? "[]") as string[];
  const [command, ...args] = argv;
  if (command === undefined) throw new Error("Set E2E_TOTP_COMMAND (make e2e-portal does).");
  const output = execFileSync(command, [...args, login], { encoding: "utf8" });
  const code = /\b(\d{6})\s*$/u.exec(output)?.[1];
  if (code === undefined) throw new Error("totp_code printed no code.");
  return code;
}

/** WCAG 2.2 AA checks on what is on screen; the page must have none. */
export async function expectAccessible(page: Page, name: string): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const problems = results.violations.map(
    (violation) => `${violation.id} (${String(violation.nodes.length)}): ${violation.help}`,
  );
  expect(problems, `accessibility problems on ${name}`).toEqual([]);
}

/** Signs in through the form (any language: the fields are found by their input purpose). */
export async function signIn(page: Page, secret = password): Promise<void> {
  await page.goto("/login");
  await page.locator('input[autocomplete="username"]').fill(login);
  await page.locator('input[autocomplete="current-password"]').fill(secret);
  await page.locator('button[type="submit"]').click();
  await expect(page).toHaveURL(/\/$/u);
}

/** English pages, whatever the service's default language. */
export async function inEnglish(page: Page): Promise<void> {
  await page.addInitScript(() => {
    if (window.localStorage.getItem("smart-iptv.language") === null) {
      window.localStorage.setItem("smart-iptv.language", "en");
    }
  });
}
