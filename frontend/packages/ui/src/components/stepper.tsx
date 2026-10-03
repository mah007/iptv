import { Check } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { useFormatters } from "../lib/format";

export interface StepperProps extends Omit<ComponentProps<"nav">, "children"> {
  /** Step names, in order. */
  steps: readonly ReactNode[];
  /** Index of the current step (0-based); earlier steps show as done. */
  current: number;
  /** Accessible name of the step list, e.g. "Steps to create a customer". */
  label: string;
}

/** Progress through a multi-step flow (e.g. a wizard): numbered steps, done ones ticked. */
export function Stepper({ steps, current, label, className, ...props }: StepperProps) {
  const { t } = useTranslation("ui");
  const format = useFormatters();
  return (
    <nav data-slot="stepper" aria-label={label} className={className} {...props}>
      <ol className="flex items-center gap-2">
        {steps.map((step, index) => {
          const done = index < current;
          const active = index === current;
          return (
            <li
              // Steps are fixed for a given flow, so the position is a stable key.
              key={index}
              aria-current={active ? "step" : undefined}
              className="flex min-w-0 items-center gap-2 [&:not(:last-child)]:flex-1"
            >
              <span
                aria-hidden="true"
                className={cn(
                  "grid size-6 shrink-0 place-items-center rounded-full border text-xs font-semibold tabular-nums transition-colors duration-150",
                  done && "border-primary bg-primary text-primary-foreground",
                  active && "border-primary text-primary",
                  !done && !active && "border-border text-muted-foreground",
                )}
              >
                {done ? <Check className="size-3.5" /> : format.number(index + 1)}
              </span>
              <span
                className={cn(
                  "truncate text-ui",
                  active ? "font-medium text-foreground" : "text-muted-foreground",
                )}
              >
                {step}
                {done ? <span className="sr-only">{` ${t("stepper.done")}`}</span> : null}
              </span>
              {index < steps.length - 1 ? (
                <span
                  aria-hidden="true"
                  className={cn("h-px min-w-4 flex-1", done ? "bg-primary" : "bg-border")}
                />
              ) : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
