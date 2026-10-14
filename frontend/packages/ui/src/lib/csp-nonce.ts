/**
 * The page's CSP nonce (ADR-0021), from `<meta property="csp-nonce">` (Vite writes the
 * tag; nginx or the dev server fills in a fresh value per page). Libraries that add a
 * `<style>` element at run time need it under the strict style-src.
 */
export function cspNonce(): string | undefined {
  if (typeof document === "undefined") return undefined;
  const meta = document.querySelector<HTMLMetaElement>('meta[property="csp-nonce"]');
  if (meta === null) return undefined;
  // Browsers hide the attribute once the page loads; the property keeps the value.
  const value = meta.nonce !== "" ? meta.nonce : (meta.getAttribute("nonce") ?? "");
  return value === "" ? undefined : value;
}

declare global {
  // Read by get-nonce (react-remove-scroll: dialogs and menus lock the page's scroll).
  var __webpack_nonce__: string | undefined;
}

/** Call once before rendering: hands the nonce to the libraries that read it globally. */
export function installCspNonce(): void {
  const nonce = cspNonce();
  if (nonce !== undefined) globalThis.__webpack_nonce__ = nonce;
}
