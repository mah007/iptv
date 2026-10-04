import { Command as CommandPrimitive } from "cmdk";
import { Search } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "../lib/cn";
import { DialogOverlay, DialogPortal } from "./dialog";

/*
 * Command menu (SPEC §8.2 command palette) on cmdk: a combobox with a filtered
 * listbox, arrow keys to move, Enter to run. `CommandDialog` puts it in a modal.
 */

export function Command({ className, ...props }: ComponentProps<typeof CommandPrimitive>) {
  return (
    <CommandPrimitive
      data-slot="command"
      className={cn(
        "flex size-full flex-col overflow-hidden rounded-card bg-popover text-popover-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function CommandInput({
  className,
  ...props
}: ComponentProps<typeof CommandPrimitive.Input>) {
  return (
    <div data-slot="command-input" className="flex items-center gap-2 border-b border-border px-3">
      <Search aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
      <CommandPrimitive.Input
        className={cn(
          "flex h-11 w-full bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
        {...props}
      />
    </div>
  );
}

export function CommandList({ className, ...props }: ComponentProps<typeof CommandPrimitive.List>) {
  return (
    <CommandPrimitive.List
      className={cn("max-h-[min(24rem,60dvh)] scroll-py-1 overflow-y-auto p-1", className)}
      {...props}
    />
  );
}

export function CommandEmpty({
  className,
  ...props
}: ComponentProps<typeof CommandPrimitive.Empty>) {
  return (
    <CommandPrimitive.Empty
      className={cn("py-6 text-center text-ui text-muted-foreground", className)}
      {...props}
    />
  );
}

export function CommandLoading({
  className,
  ...props
}: ComponentProps<typeof CommandPrimitive.Loading>) {
  return (
    <CommandPrimitive.Loading
      className={cn("px-3 py-2 text-xs text-muted-foreground", className)}
      {...props}
    />
  );
}

export function CommandGroup({
  className,
  ...props
}: ComponentProps<typeof CommandPrimitive.Group>) {
  return (
    <CommandPrimitive.Group
      className={cn(
        "overflow-hidden p-1 text-foreground [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function CommandSeparator({
  className,
  ...props
}: ComponentProps<typeof CommandPrimitive.Separator>) {
  return (
    <CommandPrimitive.Separator className={cn("-mx-1 h-px bg-border", className)} {...props} />
  );
}

export function CommandItem({ className, ...props }: ComponentProps<typeof CommandPrimitive.Item>) {
  return (
    <CommandPrimitive.Item
      className={cn(
        "relative flex cursor-pointer select-none items-center gap-2 rounded-input px-2 py-2 text-ui outline-none data-[disabled=true]:pointer-events-none data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground data-[disabled=true]:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function CommandShortcut({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      className={cn("ms-auto flex items-center gap-1 text-xs text-muted-foreground", className)}
      {...props}
    />
  );
}

export interface CommandDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The dialog's accessible name and description (visually hidden). */
  title: string;
  description: string;
  /** Filter items with cmdk (true) or show them as given (false, server-side search). */
  shouldFilter?: boolean;
  /** The input's label for assistive technology. */
  label: string;
  children: ReactNode;
}

/** The command menu in a modal: centred near the top, Escape closes, focus returns. */
export function CommandDialog({
  open,
  onOpenChange,
  title,
  description,
  shouldFilter = true,
  label,
  children,
}: CommandDialogProps) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogOverlay />
        <DialogPrimitive.Content
          data-slot="command-dialog"
          className="fixed inset-x-0 top-[12dvh] z-50 mx-auto w-[calc(100%-2rem)] max-w-xl overflow-hidden rounded-card border border-border bg-popover p-0 text-popover-foreground shadow-elevation outline-none data-[state=closed]:animate-pop-out data-[state=open]:animate-pop-in"
        >
          <DialogPrimitive.Title className="sr-only">{title}</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">
            {description}
          </DialogPrimitive.Description>
          <Command label={label} shouldFilter={shouldFilter} loop>
            {children}
          </Command>
        </DialogPrimitive.Content>
      </DialogPortal>
    </DialogPrimitive.Root>
  );
}
