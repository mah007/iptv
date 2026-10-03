import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "./button";

/** Switch between Arabic and English; the label shows the target language. */
export function LanguageToggle() {
  const { t, i18n } = useTranslation("ui");
  const next = i18n.language === "ar" ? "en" : "ar";
  return (
    <Button
      variant="ghost"
      size="sm"
      aria-label={t("language.label")}
      lang={next}
      onClick={() => void i18n.changeLanguage(next)}
    >
      <Languages aria-hidden="true" />
      {t("language.switchTo")}
    </Button>
  );
}
