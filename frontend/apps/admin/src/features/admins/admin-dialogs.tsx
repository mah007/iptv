import { zodResolver } from "@hookform/resolvers/zod";
import {
  getAdminsListQueryKey,
  getRolesListQueryKey,
  useAdminsCreate,
  useAdminsUpdate,
  useRolesList,
  type Admin,
  type AdminCreated,
  type Role,
} from "@smart-iptv/api";
import {
  Alert,
  Button,
  Checkbox,
  CopyField,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  Label,
  SecretReveal,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Skeleton,
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm, useFormContext } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { applyFieldErrors, notifyError } from "../../lib/problems";
import { roleLabel } from "./labels";

const STATUSES = ["active", "suspended", "disabled"] as const;

const createSchema = z.object({
  username: z
    .string()
    .trim()
    .min(1, "admins.validation.usernameRequired")
    .max(150, "admins.validation.usernameTooLong")
    .regex(/^[\w.@+-]+$/u, "admins.validation.usernameFormat"),
  name: z.string().trim().max(150, "admins.validation.nameTooLong"),
  email: z
    .string()
    .trim()
    .refine(
      (value) => value === "" || z.email().safeParse(value).success,
      "admins.validation.email",
    ),
  role_ids: z.array(z.string()),
});
type CreateInput = z.input<typeof createSchema>;
type CreateValues = z.output<typeof createSchema>;

const editSchema = z.object({
  name: z.string().trim().max(150, "admins.validation.nameTooLong"),
  email: z
    .string()
    .trim()
    .refine(
      (value) => value === "" || z.email().safeParse(value).success,
      "admins.validation.email",
    ),
  status: z.enum(STATUSES),
  role_ids: z.array(z.string()),
});
type EditInput = z.input<typeof editSchema>;
type EditValues = z.output<typeof editSchema>;

/** Role checkboxes bound to the form's `role_ids`. */
function RolePicker({ disabled = false }: { disabled?: boolean }) {
  const { t } = useTranslation();
  const form = useFormContext<{ role_ids: string[] }>();
  const roles = useRolesList();
  return (
    <FormField
      control={form.control}
      name="role_ids"
      render={({ field }) => (
        <FormItem>
          <FormLabel>{t("admins.fields.roles")}</FormLabel>
          {roles.isPending ? (
            <div className="grid gap-2">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-4 w-40" />
            </div>
          ) : roles.isError ? (
            <p className="text-ui text-danger-text">{t("admins.rolesError")}</p>
          ) : (
            <div className="grid gap-2 rounded-input border border-border p-3 sm:grid-cols-2">
              {roles.data.map((role: Role) => (
                <Label key={role.id} className="flex items-start gap-2 font-normal">
                  <Checkbox
                    className="mt-0.5"
                    disabled={disabled}
                    checked={field.value.includes(role.id)}
                    onCheckedChange={(checked) => {
                      field.onChange(
                        checked === true
                          ? [...field.value, role.id]
                          : field.value.filter((id) => id !== role.id),
                      );
                    }}
                  />
                  <span className="grid gap-0.5">
                    <span className="font-medium text-foreground">{roleLabel(t, role.name)}</span>
                    {role.description ? (
                      <span className="text-xs text-muted-foreground">
                        {t(`roles.descriptions.${role.name}`, { defaultValue: role.description })}
                      </span>
                    ) : null}
                  </span>
                </Label>
              ))}
            </div>
          )}
          <FormDescription>{t("admins.fields.rolesHelp")}</FormDescription>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

function ContactFields() {
  const { t } = useTranslation();
  const form = useFormContext<{ name: string; email: string }>();
  return (
    <div className="grid items-start gap-4 sm:grid-cols-2">
      <FormField
        control={form.control}
        name="name"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("admins.fields.name")}</FormLabel>
            <FormControl>
              <Input autoComplete="off" dir="auto" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name="email"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("admins.fields.email")}</FormLabel>
            <FormControl>
              <Input type="email" autoComplete="off" dir="ltr" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
    </div>
  );
}

function CreatedAdmin({ created, onDone }: { created: AdminCreated; onDone: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-4">
      <Alert tone="warning">{t("admins.create.passwordOnce")}</Alert>
      <CopyField label={t("admins.fields.username")} value={created.admin.username} />
      <SecretReveal label={t("admins.create.password")} secret={created.password} />
      <p className="text-ui text-muted-foreground">{t("admins.create.mfaNote")}</p>
      <DialogFooter>
        <Button onClick={onDone}>{t("common.done")}</Button>
      </DialogFooter>
    </div>
  );
}

function CreateAdminForm({
  onCreated,
  onCancel,
}: {
  onCreated: (created: AdminCreated) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const create = useAdminsCreate();
  const form = useForm<CreateInput, unknown, CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: { username: "", name: "", email: "", role_ids: [] },
  });
  const submit = form.handleSubmit(async (values) => {
    try {
      const created = await create.mutateAsync({
        data: {
          username: values.username,
          role_ids: values.role_ids,
          ...(values.name ? { name: values.name } : {}),
          ...(values.email ? { email: values.email } : {}),
        },
      });
      void queryClient.invalidateQueries({ queryKey: getAdminsListQueryKey() });
      void queryClient.invalidateQueries({ queryKey: getRolesListQueryKey() });
      onCreated(created);
    } catch (error) {
      const fields = applyFieldErrors(error, form.setError, {
        username: "username",
        name: "name",
        email: "email",
        role_ids: "role_ids",
      });
      if (fields.length === 0) notifyError(t, error);
    }
  });
  return (
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
          name="username"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("admins.fields.username")}</FormLabel>
              <FormControl>
                <Input
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                  dir="ltr"
                  {...field}
                />
              </FormControl>
              <FormDescription>{t("admins.fields.usernameHelp")}</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <ContactFields />
        <RolePicker />
        <DialogFooter>
          <Button type="button" variant="secondary" onClick={onCancel}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" pending={form.formState.isSubmitting}>
            {t("admins.create.submit")}
          </Button>
        </DialogFooter>
      </form>
    </Form>
  );
}

/** "Add admin": a generated password shown once; MFA is enrolled at first sign-in. */
export function CreateAdminDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const [created, setCreated] = useState<AdminCreated | null>(null);
  function close(): void {
    setCreated(null);
    onOpenChange(false);
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
        else onOpenChange(true);
      }}
    >
      <DialogContent
        className="max-w-xl"
        onInteractOutside={(event) => {
          event.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle>
            {created ? t("admins.create.doneTitle") : t("admins.create.title")}
          </DialogTitle>
          <DialogDescription>
            {created
              ? t("admins.create.doneDescription", { username: created.admin.username })
              : t("admins.create.description")}
          </DialogDescription>
        </DialogHeader>
        {open && created ? <CreatedAdmin created={created} onDone={close} /> : null}
        {open && !created ? <CreateAdminForm onCreated={setCreated} onCancel={close} /> : null}
      </DialogContent>
    </Dialog>
  );
}

function EditAdminForm({
  admin,
  self,
  onDone,
}: {
  admin: Admin;
  self: boolean;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const update = useAdminsUpdate();
  const form = useForm<EditInput, unknown, EditValues>({
    resolver: zodResolver(editSchema),
    defaultValues: {
      name: admin.name,
      email: admin.email,
      status: admin.status,
      role_ids: admin.roles.map((role) => role.id),
    },
  });
  const submit = form.handleSubmit(async (values) => {
    try {
      await update.mutateAsync({ id: admin.id, data: values });
      void queryClient.invalidateQueries({ queryKey: getAdminsListQueryKey() });
      void queryClient.invalidateQueries({ queryKey: getRolesListQueryKey() });
      toast.success(t("admins.edit.saved"));
      onDone();
    } catch (error) {
      const fields = applyFieldErrors(error, form.setError, {
        name: "name",
        email: "email",
        status: "status",
        role_ids: "role_ids",
      });
      if (fields.length === 0) notifyError(t, error);
    }
  });
  return (
    <Form {...form}>
      <form
        noValidate
        className="grid gap-4"
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <ContactFields />
        <FormField
          control={form.control}
          name="status"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("admins.fields.status")}</FormLabel>
              <Select value={field.value} onValueChange={field.onChange} disabled={self}>
                <FormControl>
                  <SelectTrigger className="sm:w-56">
                    <SelectValue />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {STATUSES.map((status) => (
                    <SelectItem key={status} value={status}>
                      {t(`ui:status.${status}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FormDescription>
                {self ? t("admins.edit.selfStatus") : t("admins.edit.statusHelp")}
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <RolePicker />
        <DialogFooter>
          <Button type="button" variant="secondary" onClick={onDone}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" pending={form.formState.isSubmitting}>
            {t("common.save")}
          </Button>
        </DialogFooter>
      </form>
    </Form>
  );
}

export function EditAdminDialog({
  admin,
  self,
  onClose,
}: {
  admin: Admin | null;
  self: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={admin !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>
            {t("admins.edit.title", { name: admin ? admin.name || admin.username : "" })}
          </DialogTitle>
          <DialogDescription>{t("admins.edit.description")}</DialogDescription>
        </DialogHeader>
        {admin ? <EditAdminForm key={admin.id} admin={admin} self={self} onDone={onClose} /> : null}
      </DialogContent>
    </Dialog>
  );
}
