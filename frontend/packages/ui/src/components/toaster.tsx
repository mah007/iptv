import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Toaster as Sonner } from "sonner";

import { useTheme } from "../theme";

export { toast } from "sonner";

/**
 * Toast host (sonner) styled with the design tokens. It follows the theme and
 * reading direction and sits at the bottom of the inline end edge.
 */
export function Toaster() {
  const { t, i18n } = useTranslation("ui");
  const [theme] = useTheme();
  const dir = i18n.dir(i18n.language);
  return (
    <Sonner
      theme={theme}
      dir={dir}
      position={dir === "rtl" ? "bottom-left" : "bottom-right"}
      containerAriaLabel={t("toast.region")}
      gap={8}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius-input, 8px)",
          fontFamily: "inherit",
        } as CSSProperties
      }
      toastOptions={{
        classNames: {
          toast: "!shadow-elevation !text-ui",
          description: "!text-muted-foreground",
          success: "[&_[data-icon]]:!text-success",
          error: "[&_[data-icon]]:!text-danger",
          warning: "[&_[data-icon]]:!text-warning",
          info: "[&_[data-icon]]:!text-info",
          actionButton: "!bg-primary !text-primary-foreground",
          cancelButton: "!bg-muted !text-foreground",
        },
      }}
    />
  );
}
