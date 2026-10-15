import { useTranslation } from "react-i18next";

import tmdbLogo from "../assets/tmdb-logo.svg";
import { cn } from "../lib/cn";

/**
 * The credits the data's licences ask for (SPEC §1.3, §11.1; ADR-0021, ADR-0023): TMDB's
 * notice and logo, TheTVDB's link, and DB-IP's link (CC BY 4.0) wherever IP geolocation
 * results are used.
 */
export function TmdbAttribution({ className }: { className?: string }) {
  const { t } = useTranslation("ui");
  return (
    <p className={cn("flex flex-wrap items-center gap-2 text-xs text-muted-foreground", className)}>
      <a
        href="https://www.themoviedb.org"
        target="_blank"
        rel="noopener noreferrer"
        className="shrink-0 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <img src={tmdbLogo} alt={t("attribution.tmdbLogo")} className="h-3 w-auto" />
      </a>
      <span>{t("attribution.tmdb")}</span>
    </p>
  );
}

export function DbipAttribution({ className }: { className?: string }) {
  const { t } = useTranslation("ui");
  return (
    <a
      href="https://db-ip.com"
      target="_blank"
      rel="noopener noreferrer"
      className={cn("text-xs text-muted-foreground underline-offset-2 hover:underline", className)}
    >
      {t("attribution.dbip")}
    </a>
  );
}

/**
 * TheTVDB's credit (ADR-0023): its licence asks for it, with a direct link, wherever its
 * metadata is shown (a series built from TheTVDB, and the About pages).
 */
export function TvdbAttribution({
  className,
  short = false,
}: {
  className?: string;
  /** The one-line form, under a title that shows TheTVDB's data. */
  short?: boolean;
}) {
  const { t } = useTranslation("ui");
  return (
    <a
      href="https://thetvdb.com/subscribe"
      target="_blank"
      rel="noopener noreferrer"
      className={cn("text-xs text-muted-foreground underline-offset-2 hover:underline", className)}
    >
      {short ? t("attribution.tvdbShort") : t("attribution.tvdb")}
    </a>
  );
}

/** Everything an About page credits. */
export function Attributions() {
  const { t } = useTranslation("ui");
  return (
    <ul className="grid gap-3">
      <li>
        <TmdbAttribution />
      </li>
      <li>
        <TvdbAttribution />
      </li>
      <li className="grid gap-0.5">
        <DbipAttribution />
        <span className="text-xs text-muted-foreground">{t("attribution.dbipLicence")}</span>
      </li>
      <li className="text-xs text-muted-foreground">{t("attribution.naturalEarth")}</li>
    </ul>
  );
}
