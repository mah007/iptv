import { Button, EmptyState } from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { CenteredLayout } from "../layout/auth-layout";
import { usePageTitle } from "../lib/page-title";

export function NotFoundPage() {
  const { t } = useTranslation();
  usePageTitle(t("notFound.title"));
  return (
    <CenteredLayout className="max-w-md">
      <EmptyState
        title={t("notFound.title")}
        description={t("notFound.description")}
        action={
          <Button asChild>
            <Link to="/">{t("notFound.home")}</Link>
          </Button>
        }
      />
    </CenteredLayout>
  );
}
