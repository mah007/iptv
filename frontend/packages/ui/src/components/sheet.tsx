import { X } from "lucide-react";
import { Dialog as SheetPrimitive } from "radix-ui";
import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { DialogOverlay } from "./dialog";

/** Slide-over panel (Radix Dialog) anchored to the inline start or end edge. */
export const Sheet = SheetPrimitive.Root;
export const SheetTrigger = SheetPrimitive.Trigger;
export const SheetClose = SheetPrimitive.Close;

const SIDE_CLASS = {
  start:
    "inset-y-0 start-0 border-e data-[state=closed]:animate-sheet-out data-[state=open]:animate-sheet-in",
  end: "inset-y-0 end-0 border-s data-[state=closed]:animate-sheet-out-end data-[state=open]:animate-sheet-in-end",
} as const;

export function SheetContent({
  className,
  children,
  side = "end",
  hideClose = false,
  ...props
}: ComponentProps<typeof SheetPrimitive.Content> & {
  side?: keyof typeof SIDE_CLASS;
  hideClose?: boolean;
}) {
  const { t } = useTranslation("ui");
  return (
    <SheetPrimitive.Portal>
      <DialogOverlay />
      <SheetPrimitive.Content
        data-slot="sheet-content"
        className={cn(
          "fixed z-50 flex h-full w-[calc(100%-3rem)] max-w-md flex-col border-border bg-popover text-popover-foreground shadow-elevation outline-none",
          SIDE_CLASS[side],
          className,
        )}
        {...props}
      >
        {children}
        {hideClose ? null : (
          <SheetPrimitive.Close
            className="absolute end-3 top-3 grid size-7 cursor-pointer place-items-center rounded-badge text-muted-foreground outline-none transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={t("dialog.close")}
          >
            <X aria-hidden="true" className="size-4" />
          </SheetPrimitive.Close>
        )}
      </SheetPrimitive.Content>
    </SheetPrimitive.Portal>
  );
}

export function SheetHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn("flex flex-col gap-1 border-b border-border px-6 py-4 pe-12", className)}
      {...props}
    />
  );
}

export function SheetBody({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("flex-1 overflow-y-auto px-6 py-4", className)} {...props} />;
}

export function SheetFooter({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "flex items-center justify-end gap-2 border-t border-border px-6 py-3",
        className,
      )}
      {...props}
    />
  );
}

export function SheetTitle({ className, ...props }: ComponentProps<typeof SheetPrimitive.Title>) {
  return (
    <SheetPrimitive.Title
      className={cn("text-base font-semibold leading-6 ltr:tracking-tight", className)}
      {...props}
    />
  );
}

export function SheetDescription({
  className,
  ...props
}: ComponentProps<typeof SheetPrimitive.Description>) {
  return (
    <SheetPrimitive.Description
      className={cn("text-ui text-muted-foreground", className)}
      {...props}
    />
  );
}
