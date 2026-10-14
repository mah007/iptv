// Semgrep's own tests for rules.yml (see rules.py).

// ruleid: iptv-forbidden-dependency-ts
import WebTorrent from "webtorrent";

export function remember(token: string, language: string): void {
  // ruleid: iptv-token-in-browser-storage
  localStorage.setItem("smart-iptv.token", token);
  // ruleid: iptv-token-in-browser-storage
  window.sessionStorage.setItem("refresh_token", token);
  // ok: iptv-token-in-browser-storage
  window.localStorage.setItem("smart-iptv.language", language);
  void WebTorrent;
}
