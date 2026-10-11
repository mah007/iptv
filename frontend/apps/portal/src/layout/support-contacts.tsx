import { cn } from "@smart-iptv/ui";
import { Mail, MessageCircle, Phone } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useBranding } from "./brand";

/** wa.me wants the number's digits only. */
function whatsappLink(number: string): string {
  return `https://wa.me/${number.replace(/\D/gu, "")}`;
}

/** The support contacts the admin set (Settings → Branding); nothing when none is. */
export function SupportContacts({ className }: { className?: string }) {
  const { t } = useTranslation();
  const support = useBranding()?.support;
  if (!support || (!support.email && !support.phone && !support.whatsapp)) return null;
  const link =
    "inline-flex items-center gap-1.5 rounded-input outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring";
  return (
    <section
      aria-label={t("support.title")}
      className={cn(
        "flex flex-wrap items-center justify-center gap-x-4 gap-y-1 text-xs text-muted-foreground",
        className,
      )}
    >
      <span>{t("support.title")}</span>
      {support.email ? (
        <a className={link} href={`mailto:${support.email}`}>
          <Mail aria-hidden="true" className="size-3.5" />
          <span dir="ltr">{support.email}</span>
        </a>
      ) : null}
      {support.phone ? (
        <a className={link} href={`tel:${support.phone}`} aria-label={t("support.phone")}>
          <Phone aria-hidden="true" className="size-3.5" />
          <span dir="ltr">{support.phone}</span>
        </a>
      ) : null}
      {support.whatsapp ? (
        <a
          className={link}
          href={whatsappLink(support.whatsapp)}
          target="_blank"
          rel="noreferrer"
          aria-label={t("support.whatsapp")}
        >
          <MessageCircle aria-hidden="true" className="size-3.5" />
          <span dir="ltr">{support.whatsapp}</span>
        </a>
      ) : null}
    </section>
  );
}
