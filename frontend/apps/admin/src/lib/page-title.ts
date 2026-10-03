import { useEffect } from "react";
import { useTranslation } from "react-i18next";

/** Set the browser tab title: "<page> · Smart IPTV Admin", or just the app name. */
export function usePageTitle(page?: string): void {
  const { t } = useTranslation();
  const app = t("title");
  useEffect(() => {
    document.title = page ? `${page} · ${app}` : app;
  }, [page, app]);
}
