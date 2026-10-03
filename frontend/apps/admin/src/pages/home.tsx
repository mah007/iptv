import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  PageHeader,
} from "@smart-iptv/ui";
import { useTranslation } from "react-i18next";

import { usePageTitle } from "../lib/page-title";

export function HomePage() {
  const { t } = useTranslation();
  usePageTitle();
  return (
    <div className="mx-auto grid w-full max-w-4xl">
      <PageHeader title={t("home.title")} description={t("home.subtitle")} />
      <Card>
        <CardHeader>
          <CardTitle>{t("home.statusTitle")}</CardTitle>
          <CardDescription>{t("home.statusDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          <Badge tone="success" dot>
            {t("home.status")}
          </Badge>
        </CardContent>
      </Card>
    </div>
  );
}
