import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Kbd,
} from "@smart-iptv/ui";
import { Fragment } from "react";
import { useTranslation } from "react-i18next";

import { keyCaps, useActiveShortcuts, type ShortcutGroup } from "../lib/shortcuts";

const GROUPS: readonly ShortcutGroup[] = ["general", "navigation", "page"];

/** Keys of one shortcut: "G then D", "⌘ K". */
function KeyList({ keys }: { keys: string }) {
  const { t } = useTranslation();
  const steps = keyCaps(keys);
  return (
    <span className="flex shrink-0 items-center gap-1" dir="ltr">
      {steps.map((caps, step) => (
        <Fragment key={`${String(step)}-${caps.join("+")}`}>
          {step > 0 ? (
            <span className="text-xs text-muted-foreground">{t("shortcuts.then")}</span>
          ) : null}
          {caps.map((cap) => (
            <Kbd key={cap}>{cap}</Kbd>
          ))}
        </Fragment>
      ))}
    </span>
  );
}

/** The `?` overlay: every shortcut active on this page, grouped. */
export function ShortcutsHelp({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const shortcuts = useActiveShortcuts(open);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("shortcuts.title")}</DialogTitle>
          <DialogDescription>{t("shortcuts.description")}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-6 sm:grid-cols-2">
          {GROUPS.map((group) => {
            const items = shortcuts.filter((shortcut) => shortcut.group === group);
            if (items.length === 0) return null;
            return (
              <section
                key={group}
                aria-labelledby={`shortcuts-${group}`}
                className="grid content-start gap-2"
              >
                <h3 id={`shortcuts-${group}`} className="text-xs font-medium text-muted-foreground">
                  {t(`shortcuts.groups.${group}`)}
                </h3>
                <dl className="grid gap-1.5">
                  {items.map((shortcut) => (
                    <div key={shortcut.keys} className="flex items-center justify-between gap-3">
                      <dt className="text-ui text-foreground">{t(shortcut.labelKey)}</dt>
                      <dd>
                        <KeyList keys={shortcut.keys} />
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}
