import { Fragment, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { diffJson, type DiffEntry, type DiffKind } from "../lib/json-diff";
import { Button } from "./button";

export interface DiffViewerProps {
  /** State before the change (null for a create). */
  before: unknown;
  /** State after the change (null for a delete). */
  after: unknown;
  className?: string;
}

function formatValue(value: unknown): string {
  if (value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

const KIND_MARK: Record<Exclude<DiffKind, "unchanged">, string> = {
  added: "+",
  removed: "−",
  changed: "~",
};

/** A dotted path that may wrap after each "." and before each "[" rather than mid-name. */
function PathLabel({ path }: { path: string }) {
  const parts = path.split(/(?<=\.)|(?=\[)/u);
  return (
    <>
      {parts.map((part, index) => (
        <Fragment key={`${String(index)}:${part}`}>
          {index > 0 ? <wbr /> : null}
          {part}
        </Fragment>
      ))}
    </>
  );
}

function ValueCell({ entry, side }: { entry: DiffEntry; side: "before" | "after" }) {
  const value = side === "before" ? entry.before : entry.after;
  const present = side === "before" ? entry.kind !== "added" : entry.kind !== "removed";
  const struck = side === "before" && (entry.kind === "removed" || entry.kind === "changed");
  const fresh = side === "after" && (entry.kind === "added" || entry.kind === "changed");
  return (
    <td
      className={cn(
        "max-w-0 px-3 py-1.5 align-top font-mono text-xs leading-5",
        struck && "bg-danger/8 text-danger-text",
        fresh && "bg-success/8 text-success-text",
        !struck && !fresh && "text-muted-foreground",
      )}
    >
      {present ? (
        <span dir="auto" className="block whitespace-pre-wrap wrap-anywhere">
          {formatValue(value) === "" ? <span className="opacity-60">""</span> : formatValue(value)}
        </span>
      ) : null}
    </td>
  );
}

/** Field-level before/after view of an audit change (SPEC §8.1 DiffViewer). */
export function DiffViewer({ before, after, className }: DiffViewerProps) {
  const { t } = useTranslation("ui");
  const [showUnchanged, setShowUnchanged] = useState(false);
  const entries = useMemo(() => diffJson(before, after), [before, after]);
  const unchangedCount = entries.filter((entry) => entry.kind === "unchanged").length;
  const visible = showUnchanged ? entries : entries.filter((entry) => entry.kind !== "unchanged");

  const counts = {
    added: entries.filter((entry) => entry.kind === "added").length,
    removed: entries.filter((entry) => entry.kind === "removed").length,
    changed: entries.filter((entry) => entry.kind === "changed").length,
  };

  return (
    <div
      data-slot="diff-viewer"
      className={cn("overflow-hidden rounded-input border border-border", className)}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border bg-muted/40 px-3 py-2 text-xs">
        <span className="font-medium text-success-text">
          {t("diff.added", { n: counts.added })}
        </span>
        <span className="font-medium text-danger-text">
          {t("diff.removed", { n: counts.removed })}
        </span>
        <span className="font-medium text-warning-text">
          {t("diff.changed", { n: counts.changed })}
        </span>
        {unchangedCount > 0 ? (
          <Button
            type="button"
            variant="link"
            size="xs"
            className="ms-auto text-xs"
            aria-pressed={showUnchanged}
            onClick={() => {
              setShowUnchanged((value) => !value);
            }}
          >
            {showUnchanged
              ? t("diff.hideUnchanged")
              : t("diff.showUnchanged", { n: unchangedCount })}
          </Button>
        ) : null}
      </div>
      {visible.length === 0 ? (
        <p className="px-3 py-6 text-center text-ui text-muted-foreground">{t("diff.noChanges")}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-start text-xs text-muted-foreground">
                <th scope="col" className="w-[30%] px-3 py-1.5 text-start font-medium">
                  {t("diff.field")}
                </th>
                <th scope="col" className="px-3 py-1.5 text-start font-medium">
                  {t("diff.before")}
                </th>
                <th scope="col" className="px-3 py-1.5 text-start font-medium">
                  {t("diff.after")}
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((entry) => (
                <tr
                  key={entry.path}
                  data-kind={entry.kind}
                  className="border-b border-border last:border-0"
                >
                  <th
                    scope="row"
                    className="max-w-0 px-3 py-1.5 text-start align-top font-mono text-xs font-medium leading-5 text-foreground"
                  >
                    <span className="flex items-baseline gap-1.5">
                      {entry.kind === "unchanged" ? (
                        <span aria-hidden="true" className="w-2 shrink-0" />
                      ) : (
                        <span aria-hidden="true" className="w-2 shrink-0 text-muted-foreground">
                          {KIND_MARK[entry.kind]}
                        </span>
                      )}
                      <span dir="ltr" className="min-w-0 wrap-break-word">
                        {entry.path ? <PathLabel path={entry.path} /> : t("diff.value")}
                      </span>
                      <span className="sr-only">{t(`diff.kind.${entry.kind}`)}</span>
                    </span>
                  </th>
                  <ValueCell entry={entry} side="before" />
                  <ValueCell entry={entry} side="after" />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
