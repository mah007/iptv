import { Button, ErrorState } from "@smart-iptv/ui";
import { Link, useRouter, type ErrorComponentProps } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { CenteredLayout } from "../layout/auth-layout";

function useRetry(reset: () => void): () => void {
  const router = useRouter();
  return () => {
    reset();
    void router.invalidate();
  };
}

/** A page failed to render or load: shown in the content area, the navigation stays usable. */
export function RouteErrorPage({ error, reset }: ErrorComponentProps) {
  const { t } = useTranslation();
  const retry = useRetry(reset);
  return (
    <div className="page-top px-4">
      <ErrorState
        className="min-h-[50vh]"
        title={t("routeError.title")}
        description={t("routeError.description")}
        error={error}
        onRetry={retry}
        action={
          <Button asChild variant="ghost" size="sm">
            <Link to="/">{t("routeError.home")}</Link>
          </Button>
        }
      />
    </div>
  );
}

/** The frame itself failed (the API is down while checking the session): a full page. */
export function RootErrorPage({ error, reset }: ErrorComponentProps) {
  const { t } = useTranslation();
  const retry = useRetry(reset);
  return (
    <CenteredLayout className="max-w-md">
      <ErrorState
        title={t("routeError.title")}
        description={t("routeError.description")}
        error={error}
        onRetry={retry}
      />
    </CenteredLayout>
  );
}
