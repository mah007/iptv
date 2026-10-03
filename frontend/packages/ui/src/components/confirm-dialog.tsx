import { AlertDialog as AlertDialogPrimitive } from "radix-ui";
import { useId, useState, type ReactNode } from "react";
import { Trans, useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { Button } from "./button";
import { dialogContentClassName, overlayClassName } from "./dialog";
import { Input } from "./input";

export interface ConfirmDialogProps {
  title: ReactNode;
  description?: ReactNode;
  /** Label of the confirm button, e.g. "Suspend customer". */
  confirmLabel: ReactNode;
  cancelLabel?: ReactNode;
  /** Danger styles the confirm button red; use it for destructive actions. */
  tone?: "danger" | "default";
  /**
   * Typed confirmation for destructive actions: the confirm button stays
   * disabled until this exact text (e.g. the customer's username) is typed.
   */
  confirmationText?: string;
  /** May return a promise: the dialog shows progress and closes only if it resolves. */
  onConfirm: () => void | Promise<void>;
  /** Element that opens the dialog, e.g. a <Button>. Omit when controlling `open`. */
  trigger?: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Extra content between the description and the buttons. */
  children?: ReactNode;
}

export function ConfirmDialog({
  title,
  description,
  confirmLabel,
  cancelLabel,
  tone = "default",
  confirmationText,
  onConfirm,
  trigger,
  open: controlledOpen,
  onOpenChange,
  children,
}: ConfirmDialogProps) {
  const { t } = useTranslation("ui");
  const inputId = useId();
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [pending, setPending] = useState(false);
  const open = controlledOpen ?? uncontrolledOpen;

  function setOpen(next: boolean): void {
    if (pending) return;
    if (!next) setTyped("");
    setUncontrolledOpen(next);
    onOpenChange?.(next);
  }

  const confirmed = confirmationText === undefined || typed === confirmationText;

  async function handleConfirm(): Promise<void> {
    if (!confirmed || pending) return;
    setPending(true);
    try {
      await onConfirm();
    } catch {
      // The caller reports the failure (toast, inline error); keep the dialog open to retry.
      setPending(false);
      return;
    }
    setPending(false);
    setTyped("");
    setUncontrolledOpen(false);
    onOpenChange?.(false);
  }

  return (
    <AlertDialogPrimitive.Root open={open} onOpenChange={setOpen}>
      {trigger ? (
        <AlertDialogPrimitive.Trigger asChild>{trigger}</AlertDialogPrimitive.Trigger>
      ) : null}
      <AlertDialogPrimitive.Portal>
        <AlertDialogPrimitive.Overlay className={overlayClassName} />
        <AlertDialogPrimitive.Content
          data-slot="confirm-dialog"
          className={cn(dialogContentClassName, "max-w-md")}
          onEscapeKeyDown={(event) => {
            if (pending) event.preventDefault();
          }}
        >
          <form
            className="grid gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              void handleConfirm();
            }}
          >
            <div className="grid gap-1.5">
              <AlertDialogPrimitive.Title className="text-base font-semibold leading-6 ltr:tracking-tight">
                {title}
              </AlertDialogPrimitive.Title>
              {description ? (
                <AlertDialogPrimitive.Description className="text-ui text-muted-foreground">
                  {description}
                </AlertDialogPrimitive.Description>
              ) : null}
            </div>
            {children}
            {confirmationText === undefined ? null : (
              <div className="grid gap-1.5">
                <label htmlFor={inputId} className="text-ui text-muted-foreground">
                  <Trans
                    t={t}
                    i18nKey="confirm.typeToConfirm"
                    values={{ text: confirmationText }}
                    components={{
                      code: (
                        <code className="rounded-[4px] bg-muted px-1 py-0.5 font-mono text-xs font-semibold text-foreground" />
                      ),
                    }}
                  />
                </label>
                <Input
                  id={inputId}
                  value={typed}
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                  dir="auto"
                  disabled={pending}
                  onChange={(event) => {
                    setTyped(event.target.value);
                  }}
                />
              </div>
            )}
            <div className="flex flex-col-reverse gap-2 pt-1 sm:flex-row sm:justify-end">
              <AlertDialogPrimitive.Cancel asChild>
                <Button type="button" variant="secondary" disabled={pending}>
                  {cancelLabel ?? t("confirm.cancel")}
                </Button>
              </AlertDialogPrimitive.Cancel>
              <Button
                type="submit"
                variant={tone === "danger" ? "danger" : "primary"}
                disabled={!confirmed}
                pending={pending}
              >
                {confirmLabel}
              </Button>
            </div>
          </form>
        </AlertDialogPrimitive.Content>
      </AlertDialogPrimitive.Portal>
    </AlertDialogPrimitive.Root>
  );
}
