import { Switch as SwitchPrimitive } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "../lib/cn";

export function Switch({ className, ...props }: ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        "peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border border-transparent p-0.5 shadow-xs outline-none transition-colors duration-150 focus-visible:ring-3 focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary data-[state=unchecked]:bg-input",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        className={cn(
          "pointer-events-none block size-4 rounded-full bg-background shadow-sm ring-0 transition-transform duration-150 ease-out data-[state=unchecked]:translate-x-0",
          // The thumb travels toward the inline end: rightwards in LTR, leftwards in RTL.
          "data-[state=checked]:translate-x-4 rtl:data-[state=checked]:-translate-x-4",
        )}
      />
    </SwitchPrimitive.Root>
  );
}
