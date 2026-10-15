import { useLegalRetrieve, type LegalDocument } from "@smart-iptv/api-portal";
import { ErrorState, Markdown, Skeleton, useFormatters } from "@smart-iptv/ui";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { usePageTitle } from "../lib/page-title";

/** The admin's privacy policy or terms (Settings → Legal, ADR-0023), public. */
function LegalPage({ document }: { document: LegalDocument }) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const language = i18n.language.startsWith("ar") ? "ar" : "en";
  const page = useLegalRetrieve(document, { lang: language });
  usePageTitle(t(`legal.${document}`));
  const other = document === "privacy" ? "terms" : "privacy";
  return (
    <article className="grid gap-6">
      {page.isError ? (
        <ErrorState
          error={page.error}
          onRetry={() => {
            void page.refetch();
          }}
        />
      ) : page.isPending ? (
        <div className="grid gap-3" aria-busy="true">
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      ) : (
        <Markdown text={page.data.body} />
      )}
      {page.data?.updated_at ? (
        <p className="text-xs text-muted-foreground">
          {t("legal.updated", { date: format.date(page.data.updated_at) })}
        </p>
      ) : null}
      <nav aria-label={t("legal.more")} className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        <Link
          to={other === "privacy" ? "/privacy" : "/terms"}
          className="text-primary underline-offset-4 outline-none hover:underline focus-visible:underline"
        >
          {t(`legal.${other}`)}
        </Link>
        <Link
          to="/"
          className="text-primary underline-offset-4 outline-none hover:underline focus-visible:underline"
        >
          {t("about.back")}
        </Link>
      </nav>
    </article>
  );
}

export function PrivacyPage() {
  return <LegalPage document="privacy" />;
}

export function TermsPage() {
  return <LegalPage document="terms" />;
}
