import type { ComponentProps } from "react";

import { cn } from "../lib/cn";

/** A keyboard key, e.g. a shortcut hint next to a search field. */
export function Kbd({ className, ...props }: ComponentProps<"kbd">) {
  return (
    <kbd
      data-slot="kbd"
      className={cn(
        "pointer-events-none inline-flex h-5 min-w-5 select-none items-center justify-center gap-0.5 rounded-[4px] border border-border bg-muted px-1 font-sans text-[11px] font-medium text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}
