import { Avatar as AvatarPrimitive } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "../lib/cn";

/** Up to two initials from a display name; works for Arabic and Latin names. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/u).filter(Boolean);
  const first = words[0];
  if (first === undefined) return "";
  const last = words.length > 1 ? words[words.length - 1] : undefined;
  const letters = [first, last].map((word) =>
    word === undefined ? "" : (Array.from(word)[0] ?? ""),
  );
  return letters.join("").toLocaleUpperCase();
}

const SIZE = { sm: "size-6 text-[10px]", md: "size-8 text-xs", lg: "size-10 text-sm" } as const;

export interface AvatarProps extends ComponentProps<typeof AvatarPrimitive.Root> {
  name: string;
  src?: string | undefined;
  size?: keyof typeof SIZE;
  /** Announce the avatar as an image of `name`. Leave off when the name is shown next to it. */
  labelled?: boolean;
}

export function Avatar({
  name,
  src,
  size = "md",
  labelled = false,
  className,
  children,
  ...props
}: AvatarProps) {
  return (
    <AvatarPrimitive.Root
      data-slot="avatar"
      {...(labelled ? { role: "img", "aria-label": name } : { "aria-hidden": true })}
      className={cn(
        "relative inline-flex shrink-0 select-none items-center justify-center overflow-hidden rounded-full bg-primary/12 font-semibold text-primary ring-1 ring-inset ring-primary/15",
        SIZE[size],
        className,
      )}
      {...props}
    >
      {src ? <AvatarPrimitive.Image src={src} alt="" className="size-full object-cover" /> : null}
      <AvatarPrimitive.Fallback delayMs={src ? 300 : 0}>
        {children ?? initials(name)}
      </AvatarPrimitive.Fallback>
    </AvatarPrimitive.Root>
  );
}
