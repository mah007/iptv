import { Check, Copy, Eye, EyeOff } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { Button } from "./button";
import { useCopy } from "./copy-field";

type Phase = "masked" | "revealed" | "hidden";

export interface SecretRevealProps {
  /** The plaintext (e.g. a new Xtream password). It exists only in the response that created it. */
  secret: string;
  label?: ReactNode;
  /** Called once the secret is hidden for good, e.g. so the parent can drop it from state. */
  onHidden?: () => void;
  className?: string;
}

/**
 * Show-once secret (SPEC §8.1). It starts masked; the admin may reveal it and
 * copy it. Once hidden again it stays masked: the plaintext is no longer
 * rendered and can't be revealed or copied a second time.
 */
export function SecretReveal({ secret, label, onHidden, className }: SecretRevealProps) {
  const { t } = useTranslation("ui");
  const id = useId();
  const [phase, setPhase] = useState<Phase>("masked");
  const { copied, copy } = useCopy();
  const labelId = `${id}-label`;
  const hintId = `${id}-hint`;
  // The mask never reveals the real length.
  const mask = "•".repeat(12);

  return (
    <div data-slot="secret-reveal" className={cn("grid gap-1.5", className)}>
      {label ? (
        <div id={labelId} className="text-ui font-medium leading-5 text-foreground">
          {label}
        </div>
      ) : null}
      <div
        className={cn(
          "flex h-(--density-control) items-center gap-1 rounded-input border ps-3 pe-1 transition-colors",
          phase === "hidden"
            ? "border-border bg-muted/60"
            : "border-input bg-transparent dark:bg-input/20",
        )}
      >
        <output
          aria-labelledby={label ? labelId : undefined}
          aria-describedby={hintId}
          aria-live="polite"
          className="min-w-0 flex-1 truncate font-mono text-ui tracking-tight"
        >
          {phase === "revealed" ? (
            // An isolated left-to-right run: the row still mirrors on Arabic pages.
            <bdi dir="ltr">{secret}</bdi>
          ) : (
            <>
              <span aria-hidden="true" className="tracking-widest text-muted-foreground">
                {mask}
              </span>
              <span className="sr-only">{t("secret.masked")}</span>
            </>
          )}
        </output>
        {phase === "masked" ? (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={() => {
              setPhase("revealed");
            }}
          >
            <Eye aria-hidden="true" />
            {t("secret.reveal")}
          </Button>
        ) : null}
        {phase === "revealed" ? (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={() => {
              setPhase("hidden");
              onHidden?.();
            }}
          >
            <EyeOff aria-hidden="true" />
            {t("secret.hide")}
          </Button>
        ) : null}
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          disabled={phase === "hidden"}
          className="text-muted-foreground hover:text-foreground"
          aria-label={copied ? t("copy.copied") : t("copy.copy")}
          onClick={() => {
            void copy(secret);
          }}
        >
          {copied ? (
            <Check aria-hidden="true" className="text-success" />
          ) : (
            <Copy aria-hidden="true" />
          )}
        </Button>
      </div>
      <p id={hintId} className="text-xs text-muted-foreground">
        {phase === "hidden" ? t("secret.goneHint") : t("secret.onceHint")}
      </p>
    </div>
  );
}
