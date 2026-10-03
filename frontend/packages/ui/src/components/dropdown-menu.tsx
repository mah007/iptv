import { Check, ChevronRight, Circle } from "lucide-react";
import { DropdownMenu as MenuPrimitive } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "../lib/cn";

export const DropdownMenu = MenuPrimitive.Root;
export const DropdownMenuTrigger = MenuPrimitive.Trigger;
export const DropdownMenuGroup = MenuPrimitive.Group;
export const DropdownMenuSub = MenuPrimitive.Sub;
export const DropdownMenuRadioGroup = MenuPrimitive.RadioGroup;

export const menuContentClassName =
  "z-50 min-w-44 overflow-y-auto overflow-x-hidden rounded-input border border-border bg-popover p-1 text-popover-foreground shadow-elevation outline-none data-[state=closed]:animate-pop-out data-[state=open]:animate-pop-in";

const itemClassName =
  "relative flex h-8 cursor-default select-none items-center gap-2 rounded-badge px-2 text-ui outline-none transition-colors focus:bg-accent focus:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 [&_svg:not([class*='text-'])]:text-muted-foreground";

export function DropdownMenuContent({
  className,
  sideOffset = 6,
  ...props
}: ComponentProps<typeof MenuPrimitive.Content>) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Content
        data-slot="dropdown-menu-content"
        sideOffset={sideOffset}
        className={cn(
          menuContentClassName,
          "max-h-(--radix-dropdown-menu-content-available-height) origin-(--radix-dropdown-menu-content-transform-origin)",
          className,
        )}
        {...props}
      />
    </MenuPrimitive.Portal>
  );
}

export function DropdownMenuItem({
  className,
  variant = "default",
  ...props
}: ComponentProps<typeof MenuPrimitive.Item> & { variant?: "default" | "danger" }) {
  return (
    <MenuPrimitive.Item
      data-slot="dropdown-menu-item"
      data-variant={variant}
      className={cn(
        itemClassName,
        "data-[variant=danger]:text-danger-text data-[variant=danger]:focus:bg-danger/10 data-[variant=danger]:[&_svg]:text-danger-text",
        className,
      )}
      {...props}
    />
  );
}

export function DropdownMenuCheckboxItem({
  className,
  children,
  ...props
}: ComponentProps<typeof MenuPrimitive.CheckboxItem>) {
  return (
    <MenuPrimitive.CheckboxItem className={cn(itemClassName, "ps-8", className)} {...props}>
      <span className="absolute start-2 flex size-4 items-center justify-center">
        <MenuPrimitive.ItemIndicator>
          <Check aria-hidden="true" className="size-4 text-primary" />
        </MenuPrimitive.ItemIndicator>
      </span>
      {children}
    </MenuPrimitive.CheckboxItem>
  );
}

export function DropdownMenuRadioItem({
  className,
  children,
  ...props
}: ComponentProps<typeof MenuPrimitive.RadioItem>) {
  return (
    <MenuPrimitive.RadioItem className={cn(itemClassName, "ps-8", className)} {...props}>
      <span className="absolute start-2 flex size-4 items-center justify-center">
        <MenuPrimitive.ItemIndicator>
          <Circle aria-hidden="true" className="size-2 fill-current text-primary" />
        </MenuPrimitive.ItemIndicator>
      </span>
      {children}
    </MenuPrimitive.RadioItem>
  );
}

export function DropdownMenuLabel({
  className,
  ...props
}: ComponentProps<typeof MenuPrimitive.Label>) {
  return (
    <MenuPrimitive.Label
      className={cn("px-2 py-1.5 text-xs font-medium text-muted-foreground", className)}
      {...props}
    />
  );
}

export function DropdownMenuSeparator({
  className,
  ...props
}: ComponentProps<typeof MenuPrimitive.Separator>) {
  return (
    <MenuPrimitive.Separator className={cn("-mx-1 my-1 h-px bg-border", className)} {...props} />
  );
}

export function DropdownMenuShortcut({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      className={cn("ms-auto text-xs tracking-widest text-muted-foreground", className)}
      {...props}
    />
  );
}

export function DropdownMenuSubTrigger({
  className,
  children,
  ...props
}: ComponentProps<typeof MenuPrimitive.SubTrigger>) {
  return (
    <MenuPrimitive.SubTrigger
      className={cn(itemClassName, "data-[state=open]:bg-accent", className)}
      {...props}
    >
      {children}
      <ChevronRight aria-hidden="true" className="ms-auto size-4 rtl:rotate-180" />
    </MenuPrimitive.SubTrigger>
  );
}

export function DropdownMenuSubContent({
  className,
  ...props
}: ComponentProps<typeof MenuPrimitive.SubContent>) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.SubContent
        className={cn(
          menuContentClassName,
          "origin-(--radix-dropdown-menu-content-transform-origin)",
          className,
        )}
        {...props}
      />
    </MenuPrimitive.Portal>
  );
}
