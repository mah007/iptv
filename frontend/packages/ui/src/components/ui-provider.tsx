import { Direction } from "radix-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { TooltipProvider } from "./tooltip";

/**
 * App-wide context for the kit: reading direction for Radix (keyboard
 * navigation, submenus) follows the UI language, and tooltips share delays.
 */
export function UiProvider({ children }: { children: ReactNode }) {
  const { i18n } = useTranslation("ui");
  return (
    <Direction.Provider dir={i18n.dir(i18n.language)}>
      <TooltipProvider delayDuration={400} skipDelayDuration={150}>
        {children}
      </TooltipProvider>
    </Direction.Provider>
  );
}
