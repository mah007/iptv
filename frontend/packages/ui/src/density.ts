import { useSyncExternalStore } from "react";

import { readPreference, writePreference } from "./lib/storage";

/** Table rows, controls and page padding shrink in compact mode (SPEC §8.1). */
export type Density = "comfortable" | "compact";

const STORAGE_KEY = "smart-iptv.density";
const listeners = new Set<() => void>();

function isDensity(value: unknown): value is Density {
  return value === "comfortable" || value === "compact";
}

/** The density on <html data-density>, which is the single source of truth. */
function readDensity(): Density {
  return document.documentElement.dataset.density === "compact" ? "compact" : "comfortable";
}

function applyDensity(density: Density): void {
  document.documentElement.dataset.density = density;
  for (const listener of listeners) listener();
}

/** Apply the remembered density (comfortable by default) before the first render. */
export function initDensity(fallback: Density = "comfortable"): Density {
  const stored = readPreference(STORAGE_KEY);
  const density = isDensity(stored) ? stored : fallback;
  applyDensity(density);
  return density;
}

/** Switch density and remember the choice for this browser. */
export function setDensity(density: Density): void {
  writePreference(STORAGE_KEY, density);
  applyDensity(density);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useDensity(): readonly [Density, (density: Density) => void] {
  const density = useSyncExternalStore(subscribe, readDensity, () => "comfortable" as const);
  return [density, setDensity] as const;
}
