import { installDomShims } from "@smart-iptv/ui/testing";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

installDomShims();

// jsdom has no layout and no IntersectionObserver: everything observed counts as on screen.
if (!("IntersectionObserver" in window)) {
  class IntersectionObserverShim {
    readonly root = null;
    readonly rootMargin = "0px";
    readonly thresholds = [0];
    constructor(private readonly callback: IntersectionObserverCallback) {}
    observe(target: Element): void {
      queueMicrotask(() => {
        this.callback(
          [{ isIntersecting: true, target } as unknown as IntersectionObserverEntry],
          this as unknown as IntersectionObserver,
        );
      });
    }
    unobserve(): void {
      // Nothing to stop.
    }
    disconnect(): void {
      // Nothing to stop.
    }
    takeRecords(): IntersectionObserverEntry[] {
      return [];
    }
  }
  Object.defineProperty(window, "IntersectionObserver", {
    configurable: true,
    writable: true,
    value: IntersectionObserverShim,
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
  document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  delete document.documentElement.dataset.theme;
});
