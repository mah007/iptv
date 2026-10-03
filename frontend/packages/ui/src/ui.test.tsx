import { fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { beforeEach, describe, expect, it } from "vitest";

import { AppShell, Button, cn, createI18n, initTheme } from "./index";

const messages = { en: { hello: "Hello" }, ar: { hello: "مرحبًا" } };

beforeEach(() => {
  delete document.documentElement.dataset.theme;
});

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

  it("blocks clicks and announces progress while pending", () => {
    render(<Button pending>Save</Button>);
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toHaveProperty("disabled", true);
    expect(button.getAttribute("aria-busy")).toBe("true");
  });
});

describe("cn", () => {
  it("keeps the custom 13px text size next to a text colour", () => {
    expect(cn("text-ui text-muted-foreground")).toBe("text-ui text-muted-foreground");
    expect(cn("text-sm", "text-ui")).toBe("text-ui");
    expect(cn("rounded-card", "rounded-input")).toBe("rounded-input");
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

  it("uses Arabic plural forms", async () => {
    const i18n = createI18n(messages);
    await i18n.changeLanguage("ar");
    expect(i18n.t("dataTable.selected", { ns: "ui", count: 2 })).toBe("صفّان محددان");
    expect(i18n.t("dataTable.selected", { ns: "ui", count: 5 })).toBe("5 صفوف محددة");
    expect(i18n.t("dataTable.selected", { ns: "ui", count: 12 })).toBe("12 صفًا محددًا");
  });
});

describe("AppShell", () => {
  it("toggles language and theme from the top bar", async () => {
    window.localStorage.setItem("smart-iptv.language", "en");
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

    fireEvent.click(screen.getByRole("button", { name: /Change language/ }));
    expect(await screen.findByText("تطوير")).toBeTruthy();
    expect(document.documentElement.dir).toBe("rtl");
  });

  it("hides the environment badge in production", () => {
    window.localStorage.setItem("smart-iptv.language", "en");
    render(
      <I18nextProvider i18n={createI18n(messages)}>
        <AppShell environment="production">content</AppShell>
      </I18nextProvider>,
    );
    expect(screen.queryByText("PROD")).toBeNull();
  });
});
