import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// Teach tailwind-merge our custom theme scales (styles.css). Without this it
// reads `text-ui` as a colour and drops it next to `text-muted-foreground`.
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      text: ["ui"],
      radius: ["card", "input", "badge"],
      shadow: ["elevation"],
    },
  },
});

/** Merge class names, letting later Tailwind utilities override earlier ones. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
