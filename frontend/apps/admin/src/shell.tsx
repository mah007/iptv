import { AppShell, type Environment } from "@smart-iptv/ui";
import { Outlet } from "@tanstack/react-router";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

const environment: Environment = import.meta.env.DEV ? "development" : "production";

export function Shell() {
  const { t, i18n } = useTranslation();
  useEffect(() => {
    document.title = t("title");
  }, [t, i18n.language]);

  return (
    <AppShell area={t("area")} environment={environment}>
      <Outlet />
    </AppShell>
  );
}
