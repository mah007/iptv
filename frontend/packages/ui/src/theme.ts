import { useCallback, useState } from "react";

import { readPreference, writePreference } from "./lib/storage";

export type Theme = "light" | "dark";

const STORAGE_KEY = "smart-iptv.theme";

function systemTheme(): Theme {
  if (typeof window.matchMedia !== "function") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

/**
 * Apply the remembered theme, or the app's default, before the first render
 * so the page never flashes the wrong colours.
 */
export function initTheme(fallback: Theme | "system"): Theme {
  const stored = readPreference(STORAGE_KEY);
  const theme =
    stored === "light" || stored === "dark"
      ? stored
      : fallback === "system"
        ? systemTheme()
        : fallback;
  applyTheme(theme);
  return theme;
}

export function useTheme(): readonly [Theme, (theme: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(currentTheme);
  const setTheme = useCallback((next: Theme) => {
    applyTheme(next);
    writePreference(STORAGE_KEY, next);
    setThemeState(next);
  }, []);
  return [theme, setTheme] as const;
}
