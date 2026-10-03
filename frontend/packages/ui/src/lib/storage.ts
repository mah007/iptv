/**
 * localStorage that never throws. Private windows and blocked site data can
 * make it unavailable, and preferences must degrade gracefully, not crash.
 */
export function readPreference(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writePreference(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Preference simply isn't remembered.
  }
}
