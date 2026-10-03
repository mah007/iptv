import type { ComponentProps } from "react";

import { cn } from "../lib/cn";

/** Shimmering placeholder for content that is loading (never a whole-page spinner). */
export function Skeleton({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden="true"
      className={cn("skeleton-shimmer rounded-badge", className)}
      {...props}
    />
  );
}
