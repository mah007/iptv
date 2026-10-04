import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";

/*
 * Keyboard shortcuts (SPEC §8.2): one window listener for the whole app.
 *
 * - `keys` is one key ("?", "/", "j", "1"), a modifier chord ("mod+k": ⌘ on
 *   macOS, Ctrl elsewhere) or a two-key sequence ("g d": press g, then d
 *   within a second).
 * - Letters match by physical key as well as by character, so shortcuts keep
 *   working with an Arabic keyboard layout.
 * - Nothing fires while the admin is typing in a field, except chords and Escape.
 * - Pages add their own with `useShortcuts`; the help overlay lists every
 *   shortcut that is active right now.
 */

export type ShortcutGroup = "general" | "navigation" | "page";

export interface Shortcut {
  keys: string;
  /** Translation key of what it does, for the help overlay. */
  labelKey: string;
  group: ShortcutGroup;
  run: () => void;
  /** Leave out of the help overlay (e.g. a second key for the same action). */
  hidden?: boolean;
}

interface ShortcutsContextValue {
  register: (id: symbol, shortcuts: readonly Shortcut[]) => void;
  unregister: (id: symbol) => void;
  /** Every active shortcut (a page's own ones replace global ones with the same keys). */
  active: () => Shortcut[];
}

const ShortcutsContext = createContext<ShortcutsContextValue | null>(null);

const SEQUENCE_TIMEOUT_MS = 1000;
const IS_MAC = typeof navigator !== "undefined" && /mac|iphone|ipad/iu.test(navigator.platform);

/** True when the key goes into a field the admin is editing. */
export function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  if (target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return true;
  if (target instanceof HTMLInputElement) {
    return !["checkbox", "radio", "button", "submit", "reset", "range", "color", "file"].includes(
      target.type,
    );
  }
  return target.getAttribute("role") === "combobox";
}

/** The character a key stands for: the typed one, or the US-layout letter of its physical key. */
export function keyOf(event: Pick<KeyboardEvent, "key" | "code">): string {
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  if (/^[\x20-\x7e]$/u.test(key)) return key;
  const physical = /^Key([A-Z])$/u.exec(event.code) ?? /^Digit(\d)$/u.exec(event.code);
  return physical?.[1]?.toLowerCase() ?? key;
}

function chordOf(event: KeyboardEvent): string | null {
  const mod = IS_MAC ? event.metaKey : event.ctrlKey;
  if (!mod || event.altKey) return null;
  return `mod+${keyOf(event)}`;
}

/** How a shortcut's keys read in the help overlay: "⌘ K" or "Ctrl K", "G then D". */
export function keyCaps(keys: string): string[][] {
  return keys.split(" ").map((step) =>
    step.split("+").map((part) => {
      if (part === "mod") return IS_MAC ? "⌘" : "Ctrl";
      return part.length === 1 ? part.toUpperCase() : part;
    }),
  );
}

export function ShortcutsProvider({
  globals,
  children,
}: {
  /** The app-wide shortcuts (navigation, palette, help). */
  globals: readonly Shortcut[];
  children: ReactNode;
}) {
  const pages = useRef(new Map<symbol, readonly Shortcut[]>());
  const globalsRef = useRef(globals);
  useEffect(() => {
    globalsRef.current = globals;
  });

  // Page shortcuts win over global ones with the same keys (j on the review queue).
  const active = useCallback(() => {
    const page = [...pages.current.values()].flat();
    const taken = new Set(page.map((shortcut) => shortcut.keys));
    return [...page, ...globalsRef.current.filter((shortcut) => !taken.has(shortcut.keys))];
  }, []);

  useEffect(() => {
    let pending: string | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const clear = () => {
      pending = null;
      clearTimeout(timer);
    };

    function onKeyDown(event: KeyboardEvent): void {
      if (event.defaultPrevented || event.isComposing) return;
      const shortcuts = active();
      const chord = chordOf(event);
      if (chord !== null) {
        const match = shortcuts.find((shortcut) => shortcut.keys === chord);
        if (match) {
          event.preventDefault();
          clear();
          match.run();
        }
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (isTyping(event.target)) return;
      // Inside an open dialog or menu, keys belong to it.
      if (
        event.target instanceof Element &&
        event.target.closest("[role=dialog], [role=alertdialog], [role=menu], [role=listbox]")
      ) {
        return;
      }
      const key = keyOf(event);
      if (key === "Shift") return;
      const sequence = pending === null ? key : `${pending} ${key}`;
      const match = shortcuts.find((shortcut) => shortcut.keys === sequence);
      if (match) {
        event.preventDefault();
        clear();
        match.run();
        return;
      }
      const starts =
        pending === null && shortcuts.some((shortcut) => shortcut.keys.startsWith(`${key} `));
      clear();
      if (starts) {
        pending = key;
        timer = setTimeout(clear, SEQUENCE_TIMEOUT_MS);
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      clear();
    };
  }, [active]);

  const value = useMemo<ShortcutsContextValue>(
    () => ({
      register: (id, shortcuts) => {
        pages.current.set(id, shortcuts);
      },
      unregister: (id) => {
        pages.current.delete(id);
      },
      active,
    }),
    [active],
  );

  return <ShortcutsContext.Provider value={value}>{children}</ShortcutsContext.Provider>;
}

/**
 * Shortcuts of a page or panel while it is mounted (and `enabled`). The list
 * is read on every key press, so handlers always see current state.
 */
export function useShortcuts(shortcuts: readonly Shortcut[], enabled = true): void {
  const context = useContext(ShortcutsContext);
  const id = useRef(Symbol("shortcuts"));
  const latest = useRef(shortcuts);
  useEffect(() => {
    latest.current = shortcuts;
  });
  // Re-register when the set of keys changes, not on every render.
  const signature = shortcuts.map((shortcut) => shortcut.keys).join("|");
  useEffect(() => {
    if (context === null || !enabled) return undefined;
    const key = id.current;
    // A stable list whose `run`s call the latest handlers.
    context.register(
      key,
      latest.current.map((shortcut, index) => ({
        ...shortcut,
        run: () => {
          latest.current[index]?.run();
        },
      })),
    );
    return () => {
      context.unregister(key);
    };
  }, [context, enabled, signature]);
}

/** Every active shortcut, read when the help overlay opens. */
export function useActiveShortcuts(open: boolean): Shortcut[] {
  const context = useContext(ShortcutsContext);
  return useMemo(
    () => (open && context ? context.active().filter((shortcut) => shortcut.hidden !== true) : []),
    [open, context],
  );
}

/** Move focus to the page's search field (the `/` shortcut). */
export function focusPageSearch(): boolean {
  const field = document.querySelector<HTMLInputElement>(
    "main [data-page-search], main input[type=search]",
  );
  if (!field) return false;
  field.focus();
  field.select();
  return true;
}

/** Focus the first keyboard-navigable table row of the page (j / k start there). */
export function focusFirstRow(): boolean {
  const row = document.querySelector<HTMLElement>("main [data-slot=data-table] tbody tr[tabindex]");
  if (!row) return false;
  row.focus();
  return true;
}
