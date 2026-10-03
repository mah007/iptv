import i18next, { type i18n as I18n } from "i18next";
import { initReactI18next } from "react-i18next";

import { readPreference, writePreference } from "./lib/storage";
import ar from "./locales/ar.json";
import en from "./locales/en.json";

export const LANGUAGES = ["en", "ar"] as const;
export type Language = (typeof LANGUAGES)[number];
export type Messages = Record<string, unknown>;

const STORAGE_KEY = "smart-iptv.language";

function isLanguage(value: string | null | undefined): value is Language {
  return value === "en" || value === "ar";
}

function initialLanguage(): Language {
  const stored = readPreference(STORAGE_KEY);
  if (isLanguage(stored)) return stored;
  return navigator.language.toLowerCase().startsWith("ar") ? "ar" : "en";
}

/** Keep <html lang dir> in sync so the browser mirrors layout and picks fonts. */
function applyToDocument(instance: I18n, language: string): void {
  document.documentElement.lang = language;
  document.documentElement.dir = instance.dir(language);
}

/**
 * Create an i18next instance with the shared `ui` namespace plus the app's own
 * messages as the default `app` namespace. Language persists per browser.
 */
export function createI18n(appMessages: Record<Language, Messages>): I18n {
  const instance = i18next.createInstance();
  void instance.use(initReactI18next).init({
    resources: {
      en: { ui: en, app: appMessages.en },
      ar: { ui: ar, app: appMessages.ar },
    },
    lng: initialLanguage(),
    fallbackLng: "en",
    supportedLngs: [...LANGUAGES],
    ns: ["app", "ui"],
    defaultNS: "app",
    interpolation: { escapeValue: false },
    initAsync: false,
  });
  applyToDocument(instance, instance.language);
  instance.on("languageChanged", (language) => {
    applyToDocument(instance, language);
    writePreference(STORAGE_KEY, language);
  });
  return instance;
}
