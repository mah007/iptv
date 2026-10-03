import { Check, Minus } from "lucide-react";
import { Checkbox as CheckboxPrimitive } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "../lib/cn";

export function Checkbox({ className, ...props }: ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        "peer grid size-4 shrink-0 cursor-pointer place-items-center rounded-[4px] border border-input bg-transparent shadow-xs outline-none transition-[color,background-color,border-color,box-shadow] duration-150 focus-visible:ring-3 focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-danger data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground data-[state=indeterminate]:border-primary data-[state=indeterminate]:bg-primary data-[state=indeterminate]:text-primary-foreground dark:bg-input/20",
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="group grid place-items-center">
        <Check aria-hidden="true" className="size-3 group-data-[state=indeterminate]:hidden" />
        <Minus
          aria-hidden="true"
          className="hidden size-3 group-data-[state=indeterminate]:block"
        />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}
