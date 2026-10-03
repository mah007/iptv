import { Button } from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useTranslation } from "react-i18next";

import { CenteredLayout } from "../features/auth/auth-layout";
import { usePageTitle } from "../lib/page-title";

/** 404 for any path the router doesn't know. */
export function NotFoundPage() {
  const { t } = useTranslation();
  usePageTitle(t("notFound.title"));
  return (
    <CenteredLayout className="max-w-md">
      <div className="grid justify-items-center gap-3 text-center">
        <p className="font-mono text-sm font-medium text-primary">{t("notFound.code")}</p>
        <h1 className="text-2xl font-semibold text-foreground ltr:tracking-tight">
          {t("notFound.title")}
        </h1>
        <p className="text-sm text-muted-foreground">{t("notFound.description")}</p>
        <Button asChild variant="secondary" className="mt-3">
          <Link to="/">
            <ArrowLeft aria-hidden="true" className="rtl:-scale-x-100" />
            {t("notFound.home")}
          </Link>
        </Button>
      </div>
    </CenteredLayout>
  );
}
