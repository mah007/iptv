import { useAdminsList, type Admin } from "@smart-iptv/api";
import {
  Avatar,
  Badge,
  Button,
  DataTable,
  FilterBar,
  PageHeader,
  RelativeTime,
  SearchInput,
  StatusBadge,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  createDataTableColumnHelper,
  paginationFromSearch,
  paginationToSearch,
} from "@smart-iptv/ui";
import { keepPreviousData } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import { Pencil, Plus, ShieldAlert, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { PermissionDenied, RequirePermission } from "../components/states";
import { CreateAdminDialog, EditAdminDialog } from "../features/admins/admin-dialogs";
import { roleLabel } from "../features/admins/labels";
import { RolesMatrix } from "../features/admins/roles-matrix";
import { ADMIN_TABS, type AdminTab, type AdminsSearch } from "../features/admins/search";
import { useCan, useMe } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { compact, type SearchPatch } from "../lib/search";

const route = getRouteApi("/app/admins");
const helper = createDataTableColumnHelper<Admin>();

function AdminsTable() {
  const { t } = useTranslation();
  const me = useMe();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Admin | null>(null);
  const pagination = paginationFromSearch(search);
  const query = useAdminsList(
    compact({ search: search.q, page: search.page, page_size: pagination.pageSize }),
    { query: { placeholderData: keepPreviousData } },
  );

  function update(patch: SearchPatch<AdminsSearch>, { keepPage = false } = {}): void {
    void navigate({
      search: (previous) =>
        compact({ ...previous, ...patch, ...(keepPage ? {} : { page: undefined }) }),
      replace: true,
    });
  }

  const columns = useMemo(
    () =>
      helper.columns([
        helper.accessor("name", {
          id: "admin",
          header: t("admins.columns.admin"),
          enableSorting: false,
          cell: ({ row }) => {
            const admin = row.original;
            return (
              <div className="flex min-w-0 items-center gap-2.5">
                <Avatar name={admin.name || admin.username} size="md" />
                <div className="grid min-w-0">
                  <span className="truncate font-medium text-foreground">
                    {admin.name || admin.username}
                    {admin.id === me?.id ? (
                      <Badge tone="primary" className="ms-2 align-middle">
                        {t("admins.you")}
                      </Badge>
                    ) : null}
                  </span>
                  <span className="ltr-value truncate text-xs text-muted-foreground" dir="ltr">
                    {admin.email ? `${admin.username} · ${admin.email}` : admin.username}
                  </span>
                </div>
              </div>
            );
          },
        }),
        helper.accessor("roles", {
          id: "roles",
          header: t("admins.columns.roles"),
          enableSorting: false,
          cell: ({ getValue }) => {
            const roles = getValue();
            return roles.length === 0 ? (
              <span className="text-muted-foreground">{t("admins.noRoles")}</span>
            ) : (
              <span className="flex flex-wrap gap-1">
                {roles.map((role) => (
                  <Badge key={role.id} tone="primary">
                    {roleLabel(t, role.name)}
                  </Badge>
                ))}
              </span>
            );
          },
        }),
        helper.accessor("mfa_enabled", {
          id: "mfa",
          header: t("admins.columns.mfa"),
          enableSorting: false,
          cell: ({ getValue }) =>
            getValue() ? (
              <Badge tone="success">
                <ShieldCheck aria-hidden="true" />
                {t("admins.mfaOn")}
              </Badge>
            ) : (
              <Badge tone="warning">
                <ShieldAlert aria-hidden="true" />
                {t("admins.mfaPending")}
              </Badge>
            ),
        }),
        helper.accessor("status", {
          id: "status",
          header: t("admins.columns.status"),
          enableSorting: false,
          cell: ({ getValue }) => <StatusBadge status={getValue()} />,
        }),
        helper.accessor("last_login", {
          id: "last_login",
          header: t("admins.columns.lastLogin"),
          enableSorting: false,
          cell: ({ row }) =>
            row.original.last_login ? (
              <span className="grid">
                <RelativeTime value={row.original.last_login} />
                {row.original.last_login_ip ? (
                  <span className="ltr-value font-mono text-xs text-muted-foreground" dir="ltr">
                    {row.original.last_login_ip}
                  </span>
                ) : null}
              </span>
            ) : (
              <span className="text-muted-foreground">{t("admins.neverSignedIn")}</span>
            ),
        }),
        helper.display({
          id: "actions",
          enableHiding: false,
          header: () => <span className="sr-only">{t("admins.columns.actions")}</span>,
          cell: ({ row }) => (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t("admins.edit.label", {
                name: row.original.name || row.original.username,
              })}
              onClick={() => {
                setEditing(row.original);
              }}
            >
              <Pencil aria-hidden="true" />
            </Button>
          ),
          meta: { align: "end", cellClassName: "w-12" },
        }),
      ]),
    [t, me?.id],
  );

  return (
    <>
      <DataTable
        label={t("admins.tabs.admins")}
        columns={columns}
        data={query.data?.results}
        getRowId={(admin) => admin.id}
        rowCount={query.data?.count}
        pagination={pagination}
        onPaginationChange={(next) => {
          update(paginationToSearch(next), { keepPage: true });
        }}
        sorting={[]}
        onSortingChange={() => undefined}
        loading={query.isPending}
        fetching={query.isFetching}
        error={query.isError}
        onRetry={() => {
          void query.refetch();
        }}
        toolbar={
          <FilterBar
            className="w-full"
            search={
              <SearchInput
                value={search.q ?? ""}
                placeholder={t("admins.search")}
                onValueChange={(value) => {
                  update({ q: value || undefined });
                }}
              />
            }
            actions={
              <Button
                size="sm"
                onClick={() => {
                  setCreating(true);
                }}
              >
                <Plus aria-hidden="true" />
                {t("admins.create.button")}
              </Button>
            }
          />
        }
      />
      <CreateAdminDialog open={creating} onOpenChange={setCreating} />
      <EditAdminDialog
        admin={editing}
        self={editing?.id === me?.id}
        onClose={() => {
          setEditing(null);
        }}
      />
    </>
  );
}

function Admins() {
  const { t } = useTranslation();
  const can = useCan();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const canAdmins = can("admins.manage");
  const tab: AdminTab = search.tab ?? (canAdmins ? "admins" : "roles");
  return (
    <>
      <PageHeader title={t("admins.title")} description={t("admins.description")} />
      <Tabs
        value={tab}
        onValueChange={(value) => {
          const next = ADMIN_TABS.find((candidate) => candidate === value);
          void navigate({ search: next && next !== "admins" ? { tab: next } : {}, replace: true });
        }}
      >
        <TabsList>
          <TabsTrigger value="admins">{t("admins.tabs.admins")}</TabsTrigger>
          <TabsTrigger value="roles">{t("admins.tabs.roles")}</TabsTrigger>
        </TabsList>
        <TabsContent value="admins">
          {canAdmins ? <AdminsTable /> : <PermissionDenied className="min-h-[40vh]" />}
        </TabsContent>
        <TabsContent value="roles">
          <RolesMatrix canManage={can("roles.manage")} />
        </TabsContent>
      </Tabs>
    </>
  );
}

export function AdminsPage() {
  const { t } = useTranslation();
  usePageTitle(t("admins.title"));
  return (
    <RequirePermission permission={["admins.manage", "roles.manage"]}>
      <Admins />
    </RequirePermission>
  );
}
