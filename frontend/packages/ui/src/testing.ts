/**
 * Helpers for the Vitest suites of the ui package and the apps
 * (`@smart-iptv/ui/testing`). Never import this from application code.
 */

const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/u;

type Messages = Record<string, unknown>;

/** Dotted paths of every leaf string, e.g. "home.title". */
export function leafKeys(messages: Messages, prefix = ""): string[] {
  return Object.entries(messages).flatMap(([key, value]) =>
    value !== null && typeof value === "object"
      ? leafKeys(value as Messages, `${prefix}${key}.`)
      : [`${prefix}${key}`],
  );
}

function baseKey(key: string): string {
  return key.replace(PLURAL_SUFFIX, "");
}

/**
 * Translation problems across languages: a message one language defines and
 * another lacks (English is the fallback, so a gap would silently show
 * English), or a plural message missing a form the language needs. Arabic
 * needs six (zero, one, two, few, many, other); English needs one and other.
 */
export function localeProblems(locales: Record<string, Messages>): string[] {
  const keysByLanguage = Object.entries(locales).map(
    ([language, messages]) => [language, leafKeys(messages)] as const,
  );
  const allBases = new Set(keysByLanguage.flatMap(([, keys]) => keys.map(baseKey)));
  const pluralBases = new Set(
    keysByLanguage.flatMap(([, keys]) =>
      keys.filter((key) => PLURAL_SUFFIX.test(key)).map(baseKey),
    ),
  );

  const problems: string[] = [];
  for (const [language, keys] of keysByLanguage) {
    const own = new Set(keys.map(baseKey));
    for (const base of allBases) {
      if (!own.has(base)) problems.push(`${language}: missing "${base}"`);
    }
    const categories = new Intl.PluralRules(language).resolvedOptions().pluralCategories;
    for (const base of pluralBases) {
      if (!own.has(base)) continue;
      for (const category of categories) {
        if (!keys.includes(`${base}_${category}`)) {
          problems.push(`${language}: "${base}" lacks the "${category}" plural form`);
        }
      }
    }
  }
  return problems.sort();
}

/**
 * Browser APIs jsdom doesn't implement but Radix primitives and Recharts use:
 * ResizeObserver, pointer capture and scrollIntoView; plus a silent canvas.
 */
export function installDomShims(): void {
  if (!("ResizeObserver" in window)) {
    class ResizeObserverShim {
      observe(): void {
        // jsdom has no layout, so sizes never change.
      }
      unobserve(): void {
        // Nothing observed.
      }
      disconnect(): void {
        // Nothing observed.
      }
    }
    Object.defineProperty(window, "ResizeObserver", {
      configurable: true,
      writable: true,
      value: ResizeObserverShim,
    });
  }
  const proto = Element.prototype as Partial<Element> & Record<string, unknown>;
  proto.hasPointerCapture ??= () => false;
  proto.setPointerCapture ??= () => undefined;
  proto.releasePointerCapture ??= () => undefined;
  proto.scrollIntoView ??= () => undefined;
  // jsdom has no canvas: report "no 2D context" quietly instead of logging
  // "not implemented" (the blurhash placeholder draws on a canvas).
  Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
    configurable: true,
    writable: true,
    value: () => null,
  });
}
