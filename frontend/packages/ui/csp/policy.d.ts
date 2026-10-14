export type CspApp = "admin" | "portal";

export interface CspOptions {
  mediaOrigins: readonly string[];
  nonce: string;
  connectExtra?: readonly string[];
  upgrade?: boolean;
}

export declare const NONCE_PLACEHOLDER: string;
export declare const REPORT_PATH: string;
export declare const PERMISSIONS_POLICY: Readonly<Record<CspApp, string>>;
export declare const REFERRER_POLICY: Readonly<Record<CspApp, string>>;
export declare function buildCsp(app: CspApp, options: CspOptions): string;
export declare function pageHeaders(app: CspApp, options: CspOptions): Record<string, string>;
export declare function parseOrigins(raw: string): string[];
export declare function renderNginx(app: CspApp): string;
export declare function cspPlugin(
  app: CspApp,
  options: { mediaOrigins: readonly string[]; hmr: readonly string[] },
): import("vite").Plugin;
