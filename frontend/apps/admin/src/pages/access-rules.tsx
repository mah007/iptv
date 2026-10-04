import {
  Card,
  CardContent,
  PageHeader,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@smart-iptv/ui";
import { getRouteApi } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { RequirePermission } from "../components/states";
import { AccessRulesTable, AddRuleButton } from "../features/access-rules/access-rules";
import { ACCESS_RULE_SCOPES } from "../features/access-rules/search";
import { usePageTitle } from "../lib/page-title";

const route = getRouteApi("/app/access-rules");

function AccessRules() {
  const { t } = useTranslation();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const scope = search.scope ?? "global";
  return (
    <div className="grid gap-4">
      <PageHeader
        className="pb-0"
        title={t("accessRules.title")}
        description={t("accessRules.description")}
        actions={scope === "global" ? <AddRuleButton user={null} /> : null}
      />
      <Tabs
        value={scope}
        onValueChange={(value) => {
          const next = ACCESS_RULE_SCOPES.find((candidate) => candidate === value);
          void navigate({
            search: next && next !== "global" ? { scope: next } : {},
            replace: true,
          });
        }}
      >
        <TabsList>
          <TabsTrigger value="global">{t("accessRules.scopes.global")}</TabsTrigger>
          <TabsTrigger value="customer">{t("accessRules.scopes.customer")}</TabsTrigger>
        </TabsList>
        <TabsContent value="global">
          <Card className="p-0">
            <CardContent className="p-0">
              <AccessRulesTable
                params={{ scope: "global" }}
                emptyTitle={t("accessRules.empty.global")}
              />
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="customer">
          <Card className="p-0">
            <CardContent className="p-0">
              <p className="px-(--density-card) pt-(--density-card) text-ui text-muted-foreground">
                {t("accessRules.customerScopeHelp")}
              </p>
              <AccessRulesTable
                params={{ scope: "customer" }}
                showCustomer
                emptyTitle={t("accessRules.empty.anyCustomer")}
              />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export function AccessRulesPage() {
  const { t } = useTranslation();
  usePageTitle(t("accessRules.title"));
  return (
    <RequirePermission permission="customers.view">
      <AccessRules />
    </RequirePermission>
  );
}
