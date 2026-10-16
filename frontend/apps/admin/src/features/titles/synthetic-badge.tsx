import { Badge } from "@smart-iptv/ui";
import { useTranslation } from "react-i18next";

export type TitleKind = "movie" | "series";

/** Metadata from the offline fixtures, not TMDB: flagged wherever a title shows. */
export function SyntheticBadge() {
  const { t } = useTranslation();
  return (
    <Badge tone="violet" title={t("titles.syntheticHelp")}>
      {t("titles.synthetic")}
    </Badge>
  );
}

/** Metadata an admin entered by hand (ADR-0026): no provider behind it. */
export function ManualBadge() {
  const { t } = useTranslation();
  return (
    <Badge tone="violet" title={t("titles.manualHelp")}>
      {t("titles.manualBadge")}
    </Badge>
  );
}
