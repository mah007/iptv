import { Card, CardContent, PageHeader } from "@smart-iptv/ui";
import { useTranslation } from "react-i18next";

import { RequirePermission } from "../components/states";
import { LockoutsTable } from "../features/lockouts/lockouts-table";
import { usePageTitle } from "../lib/page-title";

/** Security → Lockouts (SPEC §8.2, §8.3; ADR-0023). */
export function LockoutsPage() {
  const { t } = useTranslation();
  usePageTitle(t("lockouts.title"));
  return (
    <RequirePermission permission="lockouts.view">
      <PageHeader title={t("lockouts.title")} description={t("lockouts.description")} />
      <Card className="p-0">
        <CardContent className="p-0">
          <LockoutsTable />
        </CardContent>
      </Card>
    </RequirePermission>
  );
}
