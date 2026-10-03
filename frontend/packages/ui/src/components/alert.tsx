import { cva, type VariantProps } from "class-variance-authority";
import { CircleAlert, CircleCheck, Info, TriangleAlert } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "../lib/cn";

const alertVariants = cva(
  "grid grid-cols-[auto_1fr] items-start gap-x-2.5 gap-y-0.5 rounded-input border px-3 py-2.5 text-ui [&>svg]:mt-0.5 [&>svg]:size-4 [&>svg]:shrink-0",
  {
    variants: {
      tone: {
        info: "border-info/25 bg-info/8 text-info-text",
        success: "border-success/25 bg-success/8 text-success-text",
        warning: "border-warning/30 bg-warning/8 text-warning-text",
        danger: "border-danger/25 bg-danger/8 text-danger-text",
      },
    },
    defaultVariants: { tone: "info" },
  },
);

const ICONS = { info: Info, success: CircleCheck, warning: TriangleAlert, danger: CircleAlert };

export interface AlertProps
  extends Omit<ComponentProps<"div">, "title">, VariantProps<typeof alertVariants> {
  title?: ReactNode;
}

/** Inline message (e.g. a form error). Danger and warning alerts are announced. */
export function Alert({ tone, title, children, className, ...props }: AlertProps) {
  const resolved = tone ?? "info";
  const Icon = ICONS[resolved];
  return (
    <div
      data-slot="alert"
      role={resolved === "danger" || resolved === "warning" ? "alert" : "status"}
      className={cn(alertVariants({ tone }), className)}
      {...props}
    >
      <Icon aria-hidden="true" />
      <div className="grid gap-0.5">
        {title ? <p className="font-medium">{title}</p> : null}
        {children ? <div className="text-foreground/80">{children}</div> : null}
      </div>
    </div>
  );
}
