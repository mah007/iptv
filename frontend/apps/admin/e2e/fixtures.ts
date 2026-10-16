import { expect, test as base } from "@playwright/test";

import { totpCode } from "./support";

declare global {
  interface Window {
    __cspViolations?: string[];
  }
}

/*
 * Every admin test's page (ADR-0021):
 * - fails on any Content-Security-Policy violation, from the page's
 *   `securitypolicyviolation` events and Chrome's console;
 * - answers "Confirm it's you" with the next authenticator code whenever the
 *   step-up window has run out mid-suite, so journeys don't depend on timing.
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
    // "." for the apostrophe: a quote inside a role-name regex breaks Playwright's
    // selector parser once the handler chains locators inside the dialog.
    const stepUp = page.getByRole("dialog", { name: /^(Confirm it.s you|أكّد هويتك)$/u });
    await page.addLocatorHandler(stepUp, async (dialog) => {
      await dialog.getByLabel(/authenticator app|تطبيق المصادقة/u).fill(totpCode());
      await dialog.getByRole("button", { name: /^(Confirm|تأكيد)$/u }).click();
      await expect(dialog).toBeHidden();
    });

    await provide(page);

    const fromPage = page.isClosed()
      ? []
      : await page.evaluate(() => window.__cspViolations ?? []).catch(() => []);
    expect([...fromPage, ...fromConsole], "Content-Security-Policy violations").toEqual([]);
  },
});

export { expect };
