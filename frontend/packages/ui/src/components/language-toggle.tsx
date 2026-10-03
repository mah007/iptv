import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "../lib/cn";
import { Button } from "./button";

/**
 * Switch between Arabic and English. The visible label is the target language
 * in its own script; screen readers hear "Change language" before it.
 */
export function LanguageToggle({ className }: { className?: string }) {
  const { t, i18n } = useTranslation("ui");
  const next = i18n.language === "ar" ? "en" : "ar";
  return (
    <Button
      variant="ghost"
      size="sm"
      className={cn("text-muted-foreground hover:text-foreground", className)}
      onClick={() => void i18n.changeLanguage(next)}
    >
      <Languages aria-hidden="true" />
      <span className="sr-only">{t("language.label")}</span>
      <span lang={next}>{t("language.switchTo")}</span>
    </Button>
  );
}
