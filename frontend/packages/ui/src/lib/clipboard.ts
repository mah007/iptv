/**
 * Copy text with the async Clipboard API. Returns false instead of throwing
 * when the browser refuses (insecure origin, denied permission, no API), so
 * callers can tell the user to copy by hand.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  // The DOM typings claim the Clipboard API always exists; insecure origins and old browsers lack it.
  const clipboard = navigator.clipboard as Clipboard | undefined;
  if (!clipboard) return false;
  try {
    await clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
