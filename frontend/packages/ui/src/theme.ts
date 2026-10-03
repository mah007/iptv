import { useSyncExternalStore } from "react";

import { readPreference, writePreference } from "./lib/storage";

export type Theme = "light" | "dark";

const STORAGE_KEY = "smart-iptv.theme";
const listeners = new Set<() => void>();
let followingSystem = false;

function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark";
}

function systemQuery(): MediaQueryList | null {
  return typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;
}

/** The theme on <html data-theme>, which is the single source of truth. */
function readTheme(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  for (const listener of listeners) listener();
}

/** Track OS theme changes until the user picks a theme explicitly. */
function followSystemTheme(query: MediaQueryList): void {
  if (followingSystem) return;
  followingSystem = true;
  query.addEventListener("change", (event) => {
    if (isTheme(readPreference(STORAGE_KEY))) return;
    applyTheme(event.matches ? "dark" : "light");
  });
}

/**
 * Apply the remembered theme, or the app's default, before the first render
 * so the page never flashes the wrong colours.
 */
export function initTheme(fallback: Theme | "system"): Theme {
  const stored = readPreference(STORAGE_KEY);
  let theme: Theme;
  if (isTheme(stored)) {
    theme = stored;
  } else if (fallback === "system") {
    const query = systemQuery();
    theme = query?.matches ? "dark" : "light";
    if (query) followSystemTheme(query);
  } else {
    theme = fallback;
  }
  applyTheme(theme);
  return theme;
}

/** Switch theme and remember the choice for this browser. */
export function setTheme(theme: Theme): void {
  writePreference(STORAGE_KEY, theme);
  applyTheme(theme);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Current theme; every component using it re-renders when it changes. */
export function useTheme(): readonly [Theme, (theme: Theme) => void] {
  const theme = useSyncExternalStore(subscribe, readTheme, () => "light" as const);
  return [theme, setTheme] as const;
}
