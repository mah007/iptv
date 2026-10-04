import { zodResolver } from "@hookform/resolvers/zod";
import {
  getRolesListQueryKey,
  usePermissionsList,
  useRolesCreate,
  useRolesDestroy,
  useRolesList,
  useRolesUpdate,
  type Permission,
  type Role,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Lock, Plus, Trash2 } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { QueryError } from "../../components/states";
import { applyFieldErrors, notifyError } from "../../lib/problems";
import { permissionArea, permissionLabel, roleLabel } from "./labels";

const OWNER = "owner";
/** Permission areas in the order of the admin's navigation (SPEC §8.2). */
const AREA_ORDER = [
  "dashboard",
  "customers",
  "devices",
  "sessions",
  "subscriptions",
  "plans",
  "billing",
  "library",
  "settings",
  "audit",
  "roles",
  "admins",
];

const roleSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "roles.validation.nameRequired")
    .max(64, "roles.validation.nameTooLong"),
  description: z.string().trim().max(200, "roles.validation.descriptionTooLong"),
});
type RoleInput = z.input<typeof roleSchema>;
type RoleValues = z.output<typeof roleSchema>;

function NewRoleDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const create = useRolesCreate();
  const form = useForm<RoleInput, unknown, RoleValues>({
    resolver: zodResolver(roleSchema),
    defaultValues: { name: "", description: "" },
  });
  const submit = form.handleSubmit(async (values) => {
    try {
      await create.mutateAsync({
        data: { name: values.name, description: values.description, permissions: [] },
      });
      await queryClient.invalidateQueries({ queryKey: getRolesListQueryKey() });
      toast.success(t("roles.created", { name: values.name }));
      form.reset();
      onOpenChange(false);
    } catch (error) {
      const fields = applyFieldErrors(
        error,
        form.setError,
        {
          name: "name",
          description: "description",
        },
        t,
      );
      if (fields.length === 0) notifyError(t, error);
    }
  });
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) form.reset();
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("roles.new.title")}</DialogTitle>
          <DialogDescription>{t("roles.new.description")}</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            noValidate
            className="grid gap-4"
            onSubmit={(event) => {
              void submit(event);
            }}
          >
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("roles.fields.name")}</FormLabel>
                  <FormControl>
                    <Input autoComplete="off" dir="auto" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("roles.fields.description")}</FormLabel>
                  <FormControl>
                    <Input autoComplete="off" dir="auto" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  onOpenChange(false);
                }}
              >
                {t("common.cancel")}
              </Button>
              <Button type="submit" pending={form.formState.isSubmitting}>
                {t("roles.new.submit")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

function MatrixSkeleton() {
  const { t } = useTranslation();
  return (
    <Card role="status" aria-live="polite" className="grid gap-3 p-5">
      <span className="sr-only">{t("layout.loading")}</span>
      {[0, 1, 2, 3, 4, 5].map((row) => (
        <Skeleton key={row} className="h-5 w-full" />
      ))}
    </Card>
  );
}

/** Permission matrix (SPEC §8.3.15): roles as columns, permissions as rows, saved per role. */
export function RolesMatrix({ canManage }: { canManage: boolean }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const queryClient = useQueryClient();
  const roles = useRolesList();
  const permissions = usePermissionsList();
  const update = useRolesUpdate();
  const destroy = useRolesDestroy();
  /** Unsaved grants per role id. */
  const [drafts, setDrafts] = useState<Record<string, string[]>>({});
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Role | null>(null);

  const areas = useMemo(() => {
    const grouped = new Map<string, Permission[]>();
    for (const permission of permissions.data ?? []) {
      const area = permissionArea(permission.code);
      grouped.set(area, [...(grouped.get(area) ?? []), permission]);
    }
    const rank = (area: string) => {
      const index = AREA_ORDER.indexOf(area);
      return index === -1 ? AREA_ORDER.length : index;
    };
    return [...grouped.entries()].sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
  }, [permissions.data]);
  const columns = useMemo(
    () =>
      [...(roles.data ?? [])].sort((a, b) => Number(b.name === OWNER) - Number(a.name === OWNER)),
    [roles.data],
  );

  if (roles.isPending || permissions.isPending) return <MatrixSkeleton />;
  if (roles.isError || permissions.isError) {
    return (
      <Card>
        <QueryError
          error={roles.error ?? permissions.error}
          onRetry={() => {
            void roles.refetch();
            void permissions.refetch();
          }}
        />
      </Card>
    );
  }

  const allCodes = permissions.data.map((permission) => permission.code);
  const grants = (role: Role): readonly string[] =>
    role.name === OWNER ? allCodes : (drafts[role.id] ?? role.permissions ?? []);
  const dirtyRoles = roles.data.filter((role) => {
    const draft = drafts[role.id];
    if (draft === undefined) return false;
    const saved = new Set(role.permissions ?? []);
    return draft.length !== saved.size || draft.some((code) => !saved.has(code));
  });

  function toggle(role: Role, code: string, granted: boolean): void {
    const current = grants(role);
    const next = granted ? [...current, code] : current.filter((candidate) => candidate !== code);
    setDrafts({ ...drafts, [role.id]: allCodes.filter((candidate) => next.includes(candidate)) });
  }

  async function save(): Promise<void> {
    setSaving(true);
    let failed = false;
    for (const role of dirtyRoles) {
      try {
        await update.mutateAsync({ id: role.id, data: { permissions: drafts[role.id] ?? [] } });
      } catch (error) {
        failed = true;
        notifyError(t, error);
      }
    }
    await queryClient.invalidateQueries({ queryKey: getRolesListQueryKey() });
    setSaving(false);
    if (!failed) {
      setDrafts({});
      toast.success(t("roles.saved"));
    }
  }

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-ui text-muted-foreground">
          {canManage ? t("roles.matrixHelp") : t("roles.readOnly")}
        </p>
        {canManage ? (
          <div className="flex items-center gap-2">
            {dirtyRoles.length > 0 ? (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={saving}
                  onClick={() => {
                    setDrafts({});
                  }}
                >
                  {t("roles.discard")}
                </Button>
                <Button
                  size="sm"
                  pending={saving}
                  onClick={() => {
                    void save();
                  }}
                >
                  {t("roles.save", { count: dirtyRoles.length })}
                </Button>
              </>
            ) : null}
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setCreating(true);
              }}
            >
              <Plus aria-hidden="true" />
              {t("roles.new.button")}
            </Button>
          </div>
        ) : null}
      </div>
      <Card className="overflow-x-auto">
        <Table aria-label={t("roles.matrixLabel")}>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="min-w-64">{t("roles.permission")}</TableHead>
              {columns.map((role) => (
                <TableHead key={role.id} className="min-w-28 text-center align-top">
                  <div className="grid justify-items-center gap-1 py-2">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span
                          tabIndex={0}
                          className="inline-flex items-center gap-1 rounded-badge font-semibold text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          {role.name === OWNER ? (
                            <Lock aria-hidden="true" className="size-3" />
                          ) : null}
                          {roleLabel(t, role.name)}
                        </span>
                      </TooltipTrigger>
                      <TooltipContent className="max-w-64">
                        {t(`roles.descriptions.${role.name}`, {
                          defaultValue: role.description ?? "",
                        }) || roleLabel(t, role.name)}
                      </TooltipContent>
                    </Tooltip>
                    <span className="text-xs font-normal text-muted-foreground">
                      {t("roles.adminCount", {
                        count: role.admin_count,
                        formatted: format.number(role.admin_count),
                      })}
                    </span>
                    {canManage && role.name !== OWNER && role.admin_count === 0 ? (
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className="text-muted-foreground hover:text-danger-text"
                        aria-label={t("roles.delete.label", { name: roleLabel(t, role.name) })}
                        onClick={() => {
                          setDeleting(role);
                        }}
                      >
                        <Trash2 aria-hidden="true" />
                      </Button>
                    ) : null}
                  </div>
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {areas.map(([area, items]) => (
              <Fragment key={area}>
                <TableRow className="bg-muted/40 hover:bg-muted/40">
                  <TableCell
                    colSpan={roles.data.length + 1}
                    className="py-1.5 text-xs font-semibold uppercase text-muted-foreground ltr:tracking-wide"
                  >
                    {t(`roles.areas.${area}`, { defaultValue: area })}
                  </TableCell>
                </TableRow>
                {items.map((permission) => (
                  <TableRow key={permission.code}>
                    <TableCell>
                      <div className="grid gap-0.5">
                        <span className="text-foreground">
                          {permissionLabel(t, permission.code, permission.description)}
                        </span>
                        <code
                          className="ltr-value font-mono text-[11px] text-muted-foreground"
                          dir="ltr"
                        >
                          {permission.code}
                        </code>
                      </div>
                    </TableCell>
                    {columns.map((role) => {
                      const granted = grants(role).includes(permission.code);
                      return (
                        <TableCell key={role.id} className="text-center">
                          <Checkbox
                            aria-label={t("roles.grant", {
                              role: roleLabel(t, role.name),
                              permission: permissionLabel(
                                t,
                                permission.code,
                                permission.description,
                              ),
                            })}
                            checked={granted}
                            disabled={!canManage || role.name === OWNER || saving}
                            onCheckedChange={(value) => {
                              toggle(role, permission.code, value === true);
                            }}
                          />
                        </TableCell>
                      );
                    })}
                  </TableRow>
                ))}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      </Card>
      {dirtyRoles.length > 0 ? (
        <p className="flex items-center gap-2 text-ui text-muted-foreground" aria-live="polite">
          <Badge tone="warning">{t("roles.unsaved")}</Badge>
          {dirtyRoles.map((role) => roleLabel(t, role.name)).join(t("ui:separator"))}
        </p>
      ) : null}

      {canManage ? (
        <>
          <NewRoleDialog open={creating} onOpenChange={setCreating} />
          <ConfirmDialog
            open={deleting !== null}
            onOpenChange={(open) => {
              if (!open) setDeleting(null);
            }}
            title={t("roles.delete.title", { name: deleting ? roleLabel(t, deleting.name) : "" })}
            description={t("roles.delete.description")}
            confirmLabel={t("roles.delete.confirm")}
            tone="danger"
            {...(deleting ? { confirmationText: deleting.name } : {})}
            onConfirm={async () => {
              if (!deleting) return;
              try {
                await destroy.mutateAsync({ id: deleting.id });
                await queryClient.invalidateQueries({ queryKey: getRolesListQueryKey() });
                toast.success(t("roles.deleted"));
              } catch (error) {
                notifyError(t, error);
                throw error;
              }
            }}
          />
        </>
      ) : null}
    </div>
  );
}
