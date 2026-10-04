import { useCustomersRetrieve, type AccessProfile, type CustomerDetail } from "@smart-iptv/api";
import {
  Avatar,
  Badge,
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DescriptionItem,
  DescriptionList,
  PageHeader,
  RelativeTime,
  Skeleton,
  StatusBadge,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  useFormatters,
} from "@smart-iptv/ui";
import { Link, getRouteApi } from "@tanstack/react-router";
import { Pause, Pencil, Play } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { AccessRulesTable, AddRuleButton } from "../features/access-rules/access-rules";
import { CustomerActivity } from "../features/activity/activity";
import {
  EditAccessSheet,
  EditProfileDialog,
  ReactivateDialog,
  SuspendDialog,
} from "../features/customers/customer-editors";
import { DevicesPanel } from "../features/customers/devices-panel";
import { ExpiryText } from "../features/customers/expiry";
import { CustomerSessions } from "../features/customers/sessions-panel";
import { CUSTOMER_TABS, type CustomerTab } from "../features/customers/search";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";

const route = getRouteApi("/app/customers/$customerId");

type Editor = "profile" | "access" | "suspend" | "reactivate" | null;

function categoryLabel(category: AccessProfile["categories"][number], language: string): string {
  return language.startsWith("ar") && category.name_ar ? category.name_ar : category.name_en;
}

function AccessCard({
  customer,
  onEdit,
}: {
  customer: CustomerDetail;
  onEdit: (() => void) | null;
}) {
  const { t, i18n } = useTranslation();
  const format = useFormatters();
  const access = customer.access;
  const devices = customer.devices.filter((device) => device.status !== "revoked").length;
  const content = access
    ? (["allow_movies", "allow_series", "allow_live"] as const).filter(
        (key) => access[key] !== false,
      )
    : [];
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-3">
        <CardTitle>{t("customers.detail.accessTitle")}</CardTitle>
        {onEdit ? (
          <Button variant="secondary" size="sm" onClick={onEdit}>
            <Pencil aria-hidden="true" />
            {t("customers.detail.editAccess")}
          </Button>
        ) : null}
      </CardHeader>
      <CardContent>
        {access ? (
          <DescriptionList>
            <DescriptionItem label={t("customers.detail.accessStatus")}>
              <StatusBadge status={access.status} />
            </DescriptionItem>
            <DescriptionItem label={t("customers.fields.expires")}>
              {access.expires_at ? (
                <span className="inline-flex flex-wrap items-baseline gap-x-2">
                  <span>{format.dateTime(access.expires_at)}</span>
                  <ExpiryText expiresAt={access.expires_at} className="text-xs" />
                </span>
              ) : (
                <ExpiryText expiresAt={null} />
              )}
            </DescriptionItem>
            <DescriptionItem label={t("customers.access.maxStreams")}>
              <span className="tabular-nums">{format.number(access.max_streams ?? 1)}</span>
            </DescriptionItem>
            <DescriptionItem label={t("customers.access.maxDevices")}>
              <span className="tabular-nums" dir="ltr">
                {t("customers.deviceCount", {
                  count: format.number(devices),
                  max: format.number(access.max_devices ?? 2),
                })}
              </span>
            </DescriptionItem>
            <DescriptionItem label={t("customers.access.maxQuality")}>
              {t(`customers.quality.${String(access.max_quality ?? 1080)}`)}
            </DescriptionItem>
            <DescriptionItem label={t("customers.detail.policy")}>
              {t(`customers.policy.${access.concurrency_policy ?? "reject"}.title`)}
            </DescriptionItem>
            <DescriptionItem label={t("customers.access.content")}>
              {content.length === 0 ? (
                <span className="text-muted-foreground">{t("customers.detail.noContent")}</span>
              ) : (
                <span className="flex flex-wrap gap-1">
                  {content.map((key) => (
                    <Badge key={key}>{t(`customers.access.${key}`)}</Badge>
                  ))}
                </span>
              )}
            </DescriptionItem>
            <DescriptionItem label={t("customers.detail.categories")}>
              {access.categories.length === 0 ? (
                t("customers.detail.allCategories")
              ) : (
                <span className="flex flex-wrap gap-1">
                  {access.categories.map((category) => (
                    <Badge key={category.id} tone="primary">
                      {categoryLabel(category, i18n.language)}
                    </Badge>
                  ))}
                </span>
              )}
            </DescriptionItem>
          </DescriptionList>
        ) : (
          <p className="text-ui text-muted-foreground">{t("customers.detail.noAccess")}</p>
        )}
      </CardContent>
    </Card>
  );
}

function ProfileCard({ customer }: { customer: CustomerDetail }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("customers.detail.profileTitle")}</CardTitle>
      </CardHeader>
      <CardContent>
        <DescriptionList>
          <DescriptionItem label={t("customers.fields.email")}>
            {customer.email ? <span dir="ltr">{customer.email}</span> : null}
          </DescriptionItem>
          <DescriptionItem label={t("customers.fields.phone")}>
            {customer.phone ? (
              <span dir="ltr" className="tabular-nums">
                {customer.phone}
              </span>
            ) : null}
          </DescriptionItem>
          <DescriptionItem label={t("customers.fields.locale")}>
            {t(`customers.locales.${customer.locale}`)}
          </DescriptionItem>
          <DescriptionItem label={t("customers.detail.account")}>
            <span className="font-mono text-ui" dir="ltr">
              {customer.username}
            </span>
          </DescriptionItem>
          <DescriptionItem label={t("customers.detail.created")}>
            <RelativeTime value={customer.created_at} />
          </DescriptionItem>
          <DescriptionItem label={t("customers.fields.notes")}>
            {customer.notes ? (
              <span className="whitespace-pre-wrap" dir="auto">
                {customer.notes}
              </span>
            ) : null}
          </DescriptionItem>
        </DescriptionList>
      </CardContent>
    </Card>
  );
}

function DetailSkeleton() {
  const { t } = useTranslation();
  return (
    <div role="status" aria-live="polite" className="grid gap-6">
      <span className="sr-only">{t("layout.loading")}</span>
      <div className="flex items-center gap-3">
        <Skeleton className="size-10 rounded-full" />
        <div className="grid gap-2">
          <Skeleton className="h-6 w-56" />
          <Skeleton className="h-4 w-40" />
        </div>
      </div>
      <Skeleton className="h-9 w-72" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Skeleton className="h-72 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    </div>
  );
}

function CustomerView({ customer }: { customer: CustomerDetail }) {
  const { t } = useTranslation();
  const can = useCan();
  const edit = can("customers.edit");
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const [editor, setEditor] = useState<Editor>(null);
  const tab: CustomerTab = search.tab ?? "overview";
  const name = customer.name || customer.username;
  const suspended = customer.status !== "active";
  const deviceCount = customer.devices.filter((device) => device.status !== "revoked").length;

  function editorProps(kind: Exclude<Editor, null>) {
    return {
      customer,
      open: editor === kind,
      onOpenChange: (open: boolean) => {
        setEditor(open ? kind : null);
      },
    };
  }

  return (
    <>
      <PageHeader
        breadcrumbs={
          <Breadcrumb>
            <BreadcrumbList>
              <BreadcrumbItem>
                <BreadcrumbLink asChild>
                  <Link to="/customers">{t("customers.title")}</Link>
                </BreadcrumbLink>
              </BreadcrumbItem>
              <BreadcrumbSeparator />
              <BreadcrumbItem>
                <BreadcrumbPage>{name}</BreadcrumbPage>
              </BreadcrumbItem>
            </BreadcrumbList>
          </Breadcrumb>
        }
        title={
          <span className="flex flex-wrap items-center gap-3">
            <Avatar name={name} size="lg" />
            <span className="min-w-0 truncate">{name}</span>
            {customer.status === "active" ? null : <StatusBadge status={customer.status} />}
            {customer.access ? <StatusBadge status={customer.access.status} /> : null}
          </span>
        }
        description={
          <span className="inline-flex flex-wrap items-center gap-x-1.5">
            {customer.access?.expires_at ? (
              <>
                {customer.access.status === "expired"
                  ? t("customers.detail.expired")
                  : t("customers.detail.expires")}
                <ExpiryText expiresAt={customer.access.expires_at} />
              </>
            ) : (
              t("customers.detail.neverExpires")
            )}
          </span>
        }
        actions={
          edit ? (
            <>
              <Button
                variant="secondary"
                onClick={() => {
                  setEditor("profile");
                }}
              >
                <Pencil aria-hidden="true" />
                {t("customers.detail.editProfile")}
              </Button>
              {suspended ? (
                <Button
                  variant="secondary"
                  onClick={() => {
                    setEditor("reactivate");
                  }}
                >
                  <Play aria-hidden="true" className="rtl:-scale-x-100" />
                  {t("customers.detail.reactivate")}
                </Button>
              ) : (
                <Button
                  variant="secondary"
                  onClick={() => {
                    setEditor("suspend");
                  }}
                >
                  <Pause aria-hidden="true" />
                  {t("customers.detail.suspend")}
                </Button>
              )}
            </>
          ) : null
        }
      />

      <Tabs
        value={tab}
        onValueChange={(value) => {
          const next = CUSTOMER_TABS.find((candidate) => candidate === value);
          void navigate({
            search: next && next !== "overview" ? { tab: next } : {},
            replace: true,
          });
        }}
      >
        <TabsList>
          <TabsTrigger value="overview">{t("customers.detail.tabs.overview")}</TabsTrigger>
          <TabsTrigger value="devices">
            {t("customers.detail.tabs.devices")}
            <Badge className="tabular-nums">{deviceCount}</Badge>
          </TabsTrigger>
          <TabsTrigger value="sessions">{t("customers.detail.tabs.sessions")}</TabsTrigger>
          <TabsTrigger value="security">{t("customers.detail.tabs.security")}</TabsTrigger>
          <TabsTrigger value="activity">{t("customers.detail.tabs.activity")}</TabsTrigger>
        </TabsList>
        <TabsContent value="overview">
          <div className="grid items-start gap-4 lg:grid-cols-2">
            <AccessCard
              customer={customer}
              onEdit={
                edit
                  ? () => {
                      setEditor("access");
                    }
                  : null
              }
            />
            <ProfileCard customer={customer} />
          </div>
        </TabsContent>
        <TabsContent value="devices">
          <DevicesPanel customer={customer} />
        </TabsContent>
        <TabsContent value="sessions">
          <CustomerSessions customerId={customer.id} customerName={name} />
        </TabsContent>
        <TabsContent value="security">
          <Card className="p-0">
            <CardHeader className="flex-row items-start justify-between gap-3 p-(--density-card)">
              <div className="grid gap-1">
                <CardTitle>{t("accessRules.customerTitle")}</CardTitle>
                <CardDescription>{t("accessRules.customerDescription")}</CardDescription>
              </div>
              <AddRuleButton user={customer.id} />
            </CardHeader>
            <CardContent className="p-0">
              <AccessRulesTable
                params={{ user: customer.id }}
                emptyTitle={t("accessRules.empty.customer")}
              />
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="activity">
          <Card>
            <CardHeader>
              <CardTitle>{t("customers.detail.activityTitle")}</CardTitle>
              <CardDescription>{t("customers.detail.activityDescription")}</CardDescription>
            </CardHeader>
            <CardContent>
              <CustomerActivity customerId={customer.id} showChanges={can("audit.view")} />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {edit ? (
        <>
          <EditProfileDialog {...editorProps("profile")} />
          <EditAccessSheet {...editorProps("access")} />
          <SuspendDialog {...editorProps("suspend")} />
          <ReactivateDialog {...editorProps("reactivate")} />
        </>
      ) : null}
    </>
  );
}

function CustomerDetailView() {
  const { t } = useTranslation();
  const { customerId } = route.useParams();
  const query = useCustomersRetrieve(customerId);
  usePageTitle(query.data ? query.data.name || query.data.username : t("customers.detail.title"));
  if (query.isPending) return <DetailSkeleton />;
  if (query.isError) {
    return (
      <QueryError
        className="min-h-[50vh]"
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  return <CustomerView customer={query.data} />;
}

export function CustomerDetailPage() {
  return (
    <RequirePermission permission="customers.view">
      <CustomerDetailView />
    </RequirePermission>
  );
}
