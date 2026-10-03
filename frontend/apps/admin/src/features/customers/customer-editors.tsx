import { zodResolver } from "@hookform/resolvers/zod";
import {
  useCustomersAccessUpdate,
  useCustomersReactivate,
  useCustomersSuspend,
  useCustomersUpdate,
  type CustomerDetail,
} from "@smart-iptv/api";
import {
  Button,
  ConfirmDialog,
  DEFAULT_TIME_ZONE,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Form,
  Label,
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  Textarea,
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { useMe } from "../../lib/auth";
import { applyFieldErrors, notifyError } from "../../lib/problems";
import { invalidateCustomer, storeCustomer } from "./cache";
import { AccessFields, ProfileFields } from "./form-fields";
import {
  ACCESS_DEFAULTS,
  ACCESS_FIELD_PATHS,
  PROFILE_FIELD_PATHS,
  accessFormSchema,
  accessFormValues,
  accessPatch,
  profileFormSchema,
  profileFormValues,
  profilePatch,
  type AccessFormInput,
  type AccessFormValues,
  type ProfileFormInput,
  type ProfileFormValues,
} from "./schemas";

interface EditorProps {
  customer: CustomerDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function ProfileForm({ customer, onDone }: { customer: CustomerDetail; onDone: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const form = useForm<ProfileFormInput, unknown, ProfileFormValues>({
    resolver: zodResolver(profileFormSchema),
    defaultValues: profileFormValues(customer),
  });
  const update = useCustomersUpdate();
  const submit = form.handleSubmit(async (values) => {
    try {
      const updated = await update.mutateAsync({ id: customer.id, data: profilePatch(values) });
      void storeCustomer(queryClient, updated);
      toast.success(t("customers.edit.saved"));
      onDone();
    } catch (error) {
      if (applyFieldErrors(error, form.setError, PROFILE_FIELD_PATHS).length === 0) {
        notifyError(t, error);
      }
    }
  });
  return (
    <Form {...form}>
      <form
        noValidate
        className="grid gap-5"
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <ProfileFields />
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

/** Name, contact details, language and notes. */
export function EditProfileDialog({ customer, open, onOpenChange }: EditorProps) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{t("customers.edit.profileTitle")}</DialogTitle>
          <DialogDescription>{t("customers.edit.profileDescription")}</DialogDescription>
        </DialogHeader>
        {open ? (
          <ProfileForm
            customer={customer}
            onDone={() => {
              onOpenChange(false);
            }}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function AccessForm({ customer, onDone }: { customer: CustomerDetail; onDone: () => void }) {
  const { t } = useTranslation();
  const me = useMe();
  const timeZone = me?.timezone ?? DEFAULT_TIME_ZONE;
  const queryClient = useQueryClient();
  const form = useForm<AccessFormInput, unknown, AccessFormValues>({
    resolver: zodResolver(accessFormSchema),
    // No profile yet: the server's defaults, which never expire.
    defaultValues: customer.access
      ? accessFormValues(customer.access, timeZone)
      : { access: { ...ACCESS_DEFAULTS, expiry: "none" } },
  });
  const update = useCustomersAccessUpdate();
  const submit = form.handleSubmit(async (values) => {
    const patch = accessPatch(values.access, form.formState.dirtyFields.access ?? {}, timeZone);
    if (Object.keys(patch).length === 0) {
      onDone();
      return;
    }
    try {
      await update.mutateAsync({ id: customer.id, data: patch });
      await invalidateCustomer(queryClient, customer.id);
      toast.success(t("customers.edit.accessSaved"));
      onDone();
    } catch (error) {
      if (applyFieldErrors(error, form.setError, ACCESS_FIELD_PATHS).length === 0) {
        notifyError(t, error);
      }
    }
  });
  return (
    <Form {...form}>
      <form
        noValidate
        className="flex min-h-0 flex-1 flex-col"
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <SheetBody>
          <AccessFields timeZone={timeZone} />
        </SheetBody>
        <SheetFooter>
          <Button type="button" variant="secondary" onClick={onDone}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" pending={form.formState.isSubmitting}>
            {t("common.save")}
          </Button>
        </SheetFooter>
      </form>
    </Form>
  );
}

/** The access profile: expiry, limits, quality and content (ADR-0006). */
export function EditAccessSheet({ customer, open, onOpenChange }: EditorProps) {
  const { t } = useTranslation();
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        className="max-w-xl"
        onInteractOutside={(event) => {
          event.preventDefault();
        }}
      >
        <SheetHeader>
          <SheetTitle>{t("customers.edit.accessTitle")}</SheetTitle>
          <SheetDescription>{t("customers.edit.accessDescription")}</SheetDescription>
        </SheetHeader>
        {open ? (
          <AccessForm
            customer={customer}
            onDone={() => {
              onOpenChange(false);
            }}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

/** Suspend (playback stops, the account stays) with an optional reason. */
export function SuspendDialog({ customer, open, onOpenChange }: EditorProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const suspend = useCustomersSuspend();
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setReason("");
        onOpenChange(next);
      }}
      title={t("customers.suspend.title", { name: customer.name })}
      description={t("customers.suspend.description")}
      confirmLabel={t("customers.suspend.confirm")}
      tone="danger"
      onConfirm={async () => {
        try {
          const updated = await suspend.mutateAsync({
            id: customer.id,
            data: reason.trim() ? { reason: reason.trim() } : {},
          });
          void storeCustomer(queryClient, updated);
          toast.success(t("customers.suspend.done"));
        } catch (error) {
          notifyError(t, error);
          throw error;
        }
      }}
    >
      <div className="grid gap-1.5">
        <Label htmlFor={reasonId}>{t("customers.suspend.reason")}</Label>
        <Textarea
          id={reasonId}
          rows={2}
          maxLength={200}
          dir="auto"
          value={reason}
          onChange={(event) => {
            setReason(event.target.value);
          }}
        />
      </div>
    </ConfirmDialog>
  );
}

export function ReactivateDialog({ customer, open, onOpenChange }: EditorProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const reactivate = useCustomersReactivate();
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("customers.reactivate.title", { name: customer.name })}
      description={t("customers.reactivate.description")}
      confirmLabel={t("customers.reactivate.confirm")}
      onConfirm={async () => {
        try {
          const updated = await reactivate.mutateAsync({ id: customer.id });
          void storeCustomer(queryClient, updated);
          toast.success(t("customers.reactivate.done"));
        } catch (error) {
          notifyError(t, error);
          throw error;
        }
      }}
    />
  );
}
