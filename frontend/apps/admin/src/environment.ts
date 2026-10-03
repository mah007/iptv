import type { Environment } from "@smart-iptv/ui";

const ENVIRONMENTS: readonly Environment[] = ["development", "staging", "production"];

function isEnvironment(value: string): value is Environment {
  return (ENVIRONMENTS as readonly string[]).includes(value);
}

/**
 * Which environment the topbar badge shows. `VITE_APP_ENV` (set at build
 * time) wins, so a staging build can say STAGING; otherwise dev server builds
 * are development and everything else is production.
 */
export function resolveEnvironment(env: { DEV: boolean; VITE_APP_ENV?: string }): Environment {
  const configured = env.VITE_APP_ENV?.trim().toLowerCase();
  if (configured && isEnvironment(configured)) return configured;
  return env.DEV ? "development" : "production";
}

export const environment: Environment = resolveEnvironment(import.meta.env);
