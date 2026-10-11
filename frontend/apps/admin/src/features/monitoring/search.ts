import { booleanParam, compact, stringParam } from "../../lib/search";

/**
 * Where to land on the monitoring host (a path there, never another site), and whether
 * the last sign-in link failed. The API checks `next` again (ADR-0018).
 */
export function safeMonitoringPath(value: unknown): string | undefined {
  const path = stringParam(value);
  if (path === undefined || path.length > 512) return undefined;
  if (!path.startsWith("/") || path.startsWith("//") || path.startsWith("/\\")) return undefined;
  // eslint-disable-next-line no-control-regex -- control characters are exactly what it rejects
  if (/[\u0000-\u001f\u007f\\]/u.test(path)) return undefined;
  return path;
}

export function parseMonitoringSearch(raw: Record<string, unknown>) {
  return compact({ next: safeMonitoringPath(raw.next), failed: booleanParam(raw.failed) });
}

export type MonitoringSearch = ReturnType<typeof parseMonitoringSearch>;
