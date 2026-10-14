import { expect, test as base } from "@playwright/test";

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
