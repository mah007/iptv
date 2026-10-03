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
