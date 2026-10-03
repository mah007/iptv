import { RadioGroup as RadioGroupPrimitive } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "../lib/cn";

export function RadioGroup({
  className,
  ...props
}: ComponentProps<typeof RadioGroupPrimitive.Root>) {
  return (
    <RadioGroupPrimitive.Root
      data-slot="radio-group"
      className={cn("grid gap-2", className)}
      {...props}
    />
  );
}

export function RadioGroupItem({
  className,
  ...props
}: ComponentProps<typeof RadioGroupPrimitive.Item>) {
  return (
    <RadioGroupPrimitive.Item
      data-slot="radio-group-item"
      className={cn(
        "peer grid aspect-square size-4 shrink-0 cursor-pointer place-items-center rounded-full border border-input bg-transparent shadow-xs outline-none transition-[border-color,box-shadow] duration-150 focus-visible:ring-3 focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-danger data-[state=checked]:border-primary dark:bg-input/20",
        className,
      )}
      {...props}
    >
      <RadioGroupPrimitive.Indicator className="grid place-items-center">
        <span className="size-2 rounded-full bg-primary" />
      </RadioGroupPrimitive.Indicator>
    </RadioGroupPrimitive.Item>
  );
}
