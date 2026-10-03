import { Check, Copy } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { copyToClipboard } from "../lib/clipboard";
import { cn } from "../lib/cn";
import { Button } from "./button";
import { fieldClassName } from "./input";
import { Label } from "./label";

const COPIED_FEEDBACK_MS = 2000;

/**
 * Copy `value`, confirm with a toast and a check icon, or explain how to copy
 * by hand. Returns whether the copy worked.
 */
export function useCopy(): {
  copied: boolean;
  copy: (value: string, onFailure?: () => void) => Promise<boolean>;
} {
  const { t } = useTranslation("ui");
  // Time of the last successful copy; a new copy restarts the feedback timer.
  const [copiedAt, setCopiedAt] = useState<number | null>(null);

  useEffect(() => {
    if (copiedAt === null) return;
    const timer = setTimeout(() => {
      setCopiedAt(null);
    }, COPIED_FEEDBACK_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [copiedAt]);

  async function copy(value: string, onFailure?: () => void): Promise<boolean> {
    const ok = await copyToClipboard(value);
    if (ok) {
      setCopiedAt(Date.now());
      toast.success(t("copy.copied"));
    } else {
      setCopiedAt(null);
      onFailure?.();
      toast.error(t("copy.failed"));
    }
    return ok;
  }

  return { copied: copiedAt !== null, copy };
}

export interface CopyFieldProps {
  value: string;
  /** Visible label above the field. */
  label?: ReactNode;
  /** Accessible name when there is no visible label. */
  "aria-label"?: string;
  /** Monospace for codes, usernames, URLs and keys. */
  mono?: boolean;
  className?: string;
}

/** Read-only value with a copy button; the text stays selectable for manual copying. */
export function CopyField({ value, label, mono = true, className, ...props }: CopyFieldProps) {
  const { t } = useTranslation("ui");
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const { copied, copy } = useCopy();

  return (
    <div data-slot="copy-field" className={cn("grid gap-1.5", className)}>
      {label ? <Label htmlFor={id}>{label}</Label> : null}
      {/* Codes, usernames, URLs and keys read left-to-right even on Arabic pages;
          the wrapper shares that direction so the button sits at the input's end. */}
      <div className="relative" dir={mono ? "ltr" : undefined}>
        <input
          id={id}
          ref={input}
          readOnly
          value={value}
          aria-label={props["aria-label"]}
          onFocus={(event) => {
            event.currentTarget.select();
          }}
          className={cn(
            fieldClassName,
            "h-(--density-control) pe-10 ps-3 text-ui",
            mono && "font-mono tracking-tight",
          )}
        />
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          className="absolute end-1 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          aria-label={copied ? t("copy.copied") : t("copy.copy")}
          onClick={() => {
            void copy(value, () => {
              input.current?.select();
            });
          }}
        >
          {copied ? (
            <Check aria-hidden="true" className="text-success" />
          ) : (
            <Copy aria-hidden="true" />
          )}
        </Button>
      </div>
    </div>
  );
}
