import { useBrandingRetrieve } from "@smart-iptv/api";
import {
  Attributions,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  PageHeader,
} from "@smart-iptv/ui";
import { useTranslation } from "react-i18next";

import { usePageTitle } from "../lib/page-title";

/** About: the service and the credits its data's licences ask for (SPEC §1.3; ADR-0021). */
export function AboutPage() {
  const { t, i18n } = useTranslation();
  usePageTitle(t("about.title"));
  const branding = useBrandingRetrieve();
  const names = branding.data?.service_name;
  const configured = i18n.language === "ar" ? names?.ar : names?.en;
  const name = configured !== undefined && configured !== "" ? configured : t("about.defaultName");
  return (
    <div className="grid gap-(--density-page)">
      <PageHeader title={t("about.title")} description={t("about.description", { name })} />
      <Card>
        <CardHeader>
          <CardTitle>{t("about.credits")}</CardTitle>
          <CardDescription>{t("about.creditsDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          <Attributions />
        </CardContent>
      </Card>
    </div>
  );
}
