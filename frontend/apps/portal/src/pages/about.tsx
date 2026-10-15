import { Attributions } from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { useBranding } from "../layout/brand";
import { LegalLinks } from "../layout/legal-links";
import { usePageTitle } from "../lib/page-title";

/** About: the service and the credits its data's licences ask for (SPEC §1.3; ADR-0021). */
export function AboutPage() {
  const { t, i18n } = useTranslation();
  usePageTitle(t("about.title"));
  const names = useBranding()?.service_name;
  const configured = i18n.language === "ar" ? names?.ar : names?.en;
  const name = configured !== undefined && configured !== "" ? configured : t("brand");
  return (
    <div className="grid gap-6">
      <div className="grid gap-1.5">
        <h1 className="text-2xl font-semibold text-foreground">{t("about.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("about.description", { name })}</p>
      </div>
      <section className="grid gap-3 rounded-card border border-border bg-card p-5">
        <h2 className="text-sm font-semibold text-foreground">{t("about.credits")}</h2>
        <Attributions />
      </section>
      <section className="grid gap-2">
        <h2 className="text-sm font-semibold text-foreground">{t("legal.title")}</h2>
        <LegalLinks className="justify-start text-sm text-primary" />
      </section>
      <Link
        to="/"
        className="justify-self-start text-sm text-primary underline-offset-4 outline-none hover:underline focus-visible:underline"
      >
        {t("about.back")}
      </Link>
    </div>
  );
}
