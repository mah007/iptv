export type DiffKind = "added" | "removed" | "changed" | "unchanged";

export interface DiffEntry {
  /** Dotted path to the leaf, e.g. `plan.max_streams` or `tags[2]`; empty for a bare value. */
  path: string;
  kind: DiffKind;
  before?: unknown;
  after?: unknown;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Flatten JSON into path → leaf value. Empty objects and arrays are leaves. */
function flatten(value: unknown, prefix: string, out: Map<string, unknown>): void {
  if (Array.isArray(value) && value.length > 0) {
    value.forEach((item, index) => {
      flatten(item, `${prefix}[${String(index)}]`, out);
    });
    return;
  }
  if (isPlainObject(value) && Object.keys(value).length > 0) {
    for (const [key, item] of Object.entries(value)) {
      flatten(item, prefix ? `${prefix}.${key}` : key, out);
    }
    return;
  }
  out.set(prefix, value);
}

function leaves(value: unknown): Map<string, unknown> {
  const out = new Map<string, unknown>();
  // An absent side (e.g. `before` of a create) contributes no leaves.
  if (value !== null && value !== undefined) flatten(value, "", out);
  return out;
}

function sameLeaf(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * Leaf-level diff of two JSON values (audit `before`/`after`), in a stable
 * order: paths of `before` first, then paths only `after` has.
 */
export function diffJson(before: unknown, after: unknown): DiffEntry[] {
  const left = leaves(before);
  const right = leaves(after);
  const entries: DiffEntry[] = [];
  for (const [path, value] of left) {
    if (!right.has(path)) {
      entries.push({ path, kind: "removed", before: value });
    } else {
      const next = right.get(path);
      entries.push({
        path,
        kind: sameLeaf(value, next) ? "unchanged" : "changed",
        before: value,
        after: next,
      });
    }
  }
  for (const [path, value] of right) {
    if (!left.has(path)) entries.push({ path, kind: "added", after: value });
  }
  return entries;
}
