import type { ComponentProps } from "react";

import { cn } from "../lib/cn";

/** Shared look of text-like controls: hairline border, accent focus ring, danger when invalid. */
export const fieldClassName =
  "w-full min-w-0 rounded-input border border-input bg-transparent text-sm text-foreground shadow-xs outline-none transition-[color,border-color,box-shadow] duration-150 ease-out placeholder:text-muted-foreground/80 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/20 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-danger aria-invalid:focus-visible:ring-danger/20 dark:bg-input/20";

export function Input({ className, type = "text", ...props }: ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        fieldClassName,
        "flex h-(--density-control) px-3 py-1 file:me-3 file:border-0 file:bg-transparent file:text-ui file:font-medium",
        className,
      )}
      {...props}
    />
  );
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(fieldClassName, "flex min-h-20 resize-y px-3 py-2 leading-6", className)}
      {...props}
    />
  );
}
