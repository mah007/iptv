import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AppShell, Button, createI18n, initTheme } from "./index";

const messages = { en: { hello: "Hello" }, ar: { hello: "مرحبًا" } };

beforeEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
});
afterEach(cleanup);

describe("Button", () => {
  it("renders a primary button by default", () => {
    render(<Button>Save</Button>);
    const button = screen.getByRole("button", { name: "Save" });
    expect(button.className).toContain("bg-primary");
  });

  it("can style a child element instead of a button", () => {
    render(
      <Button asChild variant="outline">
        <a href="/x">Link</a>
      </Button>,
    );
    const link = screen.getByRole("link", { name: "Link" });
    expect(link.className).toContain("border");
  });
});

describe("createI18n", () => {
  it("defaults to English, left-to-right", () => {
    const i18n = createI18n(messages);
    expect(i18n.t("hello")).toBe("Hello");
    expect(document.documentElement.dir).toBe("ltr");
    expect(document.documentElement.lang).toBe("en");
  });

  it("switches the document to right-to-left Arabic and remembers it", async () => {
    const i18n = createI18n(messages);
    await i18n.changeLanguage("ar");
    expect(i18n.t("hello")).toBe("مرحبًا");
    expect(document.documentElement.dir).toBe("rtl");
    expect(window.localStorage.getItem("smart-iptv.language")).toBe("ar");
    expect(createI18n(messages).language).toBe("ar");
  });
});

describe("AppShell", () => {
  it("toggles language and theme from the top bar", async () => {
    initTheme("light");
    const i18n = createI18n(messages);
    render(
      <I18nextProvider i18n={i18n}>
        <AppShell environment="development">content</AppShell>
      </I18nextProvider>,
    );
    expect(screen.getByText("DEV")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(document.documentElement.dataset.theme).toBe("dark");

    fireEvent.click(screen.getByRole("button", { name: "Change language" }));
    expect(await screen.findByText("تطوير")).toBeTruthy();
    expect(document.documentElement.dir).toBe("rtl");
  });
});
