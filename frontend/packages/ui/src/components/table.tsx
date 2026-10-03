import type { ComponentProps } from "react";

import { cn } from "../lib/cn";

/** Table primitives: hairline rows, sticky header, density-aware row height. */
export function Table({
  className,
  containerClassName,
  ...props
}: ComponentProps<"table"> & { containerClassName?: string }) {
  return (
    <div
      data-slot="table-container"
      className={cn("relative w-full overflow-auto", containerClassName)}
    >
      <table
        className={cn("w-full caption-bottom border-collapse text-ui", className)}
        {...props}
      />
    </div>
  );
}

export function TableHeader({ className, ...props }: ComponentProps<"thead">) {
  return (
    <thead
      className={cn(
        "sticky top-0 z-10 bg-card/95 backdrop-blur-sm [&_tr]:border-b [&_tr]:border-border",
        className,
      )}
      {...props}
    />
  );
}

export function TableBody({ className, ...props }: ComponentProps<"tbody">) {
  return <tbody className={cn("[&_tr:last-child]:border-0", className)} {...props} />;
}

export function TableFooter({ className, ...props }: ComponentProps<"tfoot">) {
  return (
    <tfoot
      className={cn(
        "border-t border-border bg-muted/50 font-medium [&>tr]:last:border-b-0",
        className,
      )}
      {...props}
    />
  );
}

export function TableRow({ className, ...props }: ComponentProps<"tr">) {
  return (
    <tr
      className={cn(
        "border-b border-border transition-colors duration-150 hover:bg-muted/50 data-[state=selected]:bg-primary/5",
        className,
      )}
      {...props}
    />
  );
}

export function TableHead({ className, ...props }: ComponentProps<"th">) {
  return (
    <th
      className={cn(
        "h-9 whitespace-nowrap px-3 text-start align-middle text-xs font-medium text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function TableCell({ className, ...props }: ComponentProps<"td">) {
  return (
    <td
      className={cn("h-(--density-row) px-3 py-1 align-middle text-foreground", className)}
      {...props}
    />
  );
}

export function TableCaption({ className, ...props }: ComponentProps<"caption">) {
  return <caption className={cn("mt-3 text-ui text-muted-foreground", className)} {...props} />;
}
