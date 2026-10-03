import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "../lib/cn";

export const badgeVariants = cva(
  "inline-flex h-5 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-badge px-1.5 text-xs font-medium ring-1 ring-inset [&_svg]:size-3 [&_svg]:shrink-0",
  {
    variants: {
      tone: {
        neutral: "bg-muted text-neutral-text ring-border",
        primary: "bg-primary/10 text-primary ring-primary/20",
        success: "bg-success/10 text-success-text ring-success/20 dark:bg-success/15",
        warning: "bg-warning/10 text-warning-text ring-warning/25 dark:bg-warning/15",
        danger: "bg-danger/10 text-danger-text ring-danger/20 dark:bg-danger/15",
        info: "bg-info/10 text-info-text ring-info/20 dark:bg-info/15",
        violet: "bg-violet/10 text-violet-text ring-violet/20 dark:bg-violet/15",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export type BadgeTone = NonNullable<VariantProps<typeof badgeVariants>["tone"]>;

export interface BadgeProps extends ComponentProps<"span">, VariantProps<typeof badgeVariants> {
  /** Show a small dot in the tone's solid colour before the label. */
  dot?: boolean;
}

const DOT_COLOUR: Record<BadgeTone, string> = {
  neutral: "bg-neutral",
  primary: "bg-primary",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  info: "bg-info",
  violet: "bg-violet",
};

export function Badge({ className, tone, dot = false, children, ...props }: BadgeProps) {
  return (
    <span data-slot="badge" className={cn(badgeVariants({ tone }), className)} {...props}>
      {dot ? (
        <span
          aria-hidden="true"
          className={cn("size-1.5 shrink-0 rounded-full", DOT_COLOUR[tone ?? "neutral"])}
        />
      ) : null}
      {children}
    </span>
  );
}
