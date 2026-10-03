import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { initDensity } from "../density";
import { nth, renderWithUi } from "../test-utils";
import { initTheme, setTheme } from "../theme";
import { DensityToggle } from "./density-toggle";
import { LanguageToggle } from "./language-toggle";
import { ThemeToggle } from "./theme-toggle";

beforeEach(() => {
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.density;
});

describe("LanguageToggle", () => {
  it("switches to Arabic right-to-left and back, remembering the choice", async () => {
    const user = userEvent.setup();
    const { i18n } = renderWithUi(<LanguageToggle />);
    const button = screen.getByRole("button", { name: /Change language/ });
    expect(button.textContent).toContain("العربية");

    await user.click(button);
    expect(i18n.language).toBe("ar");
    expect(document.documentElement.dir).toBe("rtl");
    expect(document.documentElement.lang).toBe("ar");
    expect(window.localStorage.getItem("smart-iptv.language")).toBe("ar");

    await user.click(screen.getByRole("button", { name: /تغيير اللغة/ }));
    expect(i18n.language).toBe("en");
    expect(document.documentElement.dir).toBe("ltr");
  });
});

describe("ThemeToggle", () => {
  it("toggles the theme, persists it, and keeps every toggle in sync", async () => {
    const user = userEvent.setup();
    initTheme("light");
    renderWithUi(
      <>
        <ThemeToggle />
        <ThemeToggle />
      </>,
    );
    await user.click(nth(screen.getAllByRole("button", { name: "Switch to dark theme" }), 0));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("smart-iptv.theme")).toBe("dark");
    expect(screen.getAllByRole("button", { name: "Switch to light theme" })).toHaveLength(2);

    act(() => {
      setTheme("light");
    });
    expect(screen.getAllByRole("button", { name: "Switch to dark theme" })).toHaveLength(2);
  });

  it("prefers the remembered theme over the app default", () => {
    window.localStorage.setItem("smart-iptv.theme", "dark");
    expect(initTheme("light")).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});

describe("DensityToggle", () => {
  it("switches between comfortable and compact and remembers it", async () => {
    const user = userEvent.setup();
    expect(initDensity()).toBe("comfortable");
    renderWithUi(<DensityToggle />);

    await user.click(screen.getByRole("button", { name: "Switch to compact density" }));
    expect(document.documentElement.dataset.density).toBe("compact");
    expect(window.localStorage.getItem("smart-iptv.density")).toBe("compact");

    await user.click(screen.getByRole("button", { name: "Switch to comfortable density" }));
    expect(document.documentElement.dataset.density).toBe("comfortable");
    expect(initDensity()).toBe("comfortable");
  });
});
