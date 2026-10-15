import { cn } from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

/** The privacy policy and terms (ADR-0023): footers, About and the checkout link here. */
export function LegalLinks({ className }: { className?: string }) {
  const { t } = useTranslation();
  const link = "underline-offset-4 hover:text-foreground hover:underline";
  return (
    <p className={cn("flex flex-wrap justify-center gap-x-4 gap-y-1", className)}>
      <Link to="/privacy" className={link}>
        {t("legal.privacy")}
      </Link>
      <Link to="/terms" className={link}>
        {t("legal.terms")}
      </Link>
    </p>
  );
}
