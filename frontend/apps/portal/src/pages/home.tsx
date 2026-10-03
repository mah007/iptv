import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@smart-iptv/ui";
import { CircleCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

export function HomePage() {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("home.title")}</CardTitle>
        <CardDescription>{t("home.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent>
        <Badge tone="success">
          <CircleCheck aria-hidden="true" className="size-3.5" />
          {t("home.status")}
        </Badge>
      </CardContent>
    </Card>
  );
}
