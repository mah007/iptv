import { zodResolver } from "@hookform/resolvers/zod";
import {
  getEdgesListQueryKey,
  useEdgesCreate,
  useEdgesUpdate,
  type Edge,
  type EdgeCreated,
} from "@smart-iptv/api";
import {
  Button,
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  Switch,
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useId } from "react";
import { useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { applyFieldErrors, notifyError } from "../../lib/problems";
import { CanaryPicker } from "./canary-picker";
import {
  AUDIENCES,
  EDGE_FIELD_PATHS,
  STORAGES,
  edgeFormSchema,
  edgeFormValues,
  edgeRequest,
  newEdgeValues,
  type EdgeFormInput,
  type EdgeFormValues,
  type NewKind,
} from "./schemas";

export type EdgeSheetTarget = { edge: Edge } | { kind: NewKind };

function EdgeForm({
  target,
  origins,
  onDone,
  onCreated,
}: {
  target: EdgeSheetTarget;
  origins: Edge[];
  onDone: () => void;
  onCreated: (created: EdgeCreated) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const editing = "edge" in target ? target.edge : null;
  const form = useForm<EdgeFormInput, unknown, EdgeFormValues>({
    resolver: zodResolver(edgeFormSchema),
    defaultValues: editing
      ? edgeFormValues(editing)
      : newEdgeValues((target as { kind: NewKind }).kind),
  });
  const kind = useWatch({ control: form.control, name: "kind" });
  const audience = useWatch({ control: form.control, name: "audience" });
  const canaryLabel = useId();
  const create = useEdgesCreate();
  const update = useEdgesUpdate();
  const submit = form.handleSubmit(async (values) => {
    try {
      const data = edgeRequest(values, editing === null);
      let changedCsp = false;
      if (editing) {
        const saved = await update.mutateAsync({ id: editing.id, data });
        changedCsp = saved.csp_changed;
        toast.success(t("edges.form.saved", { name: values.name }));
      } else {
        const created = await create.mutateAsync({ data });
        changedCsp = created.csp_changed;
        onCreated(created);
      }
      if (changedCsp) toast.info(t("edges.form.cspChanged"));
      await queryClient.invalidateQueries({ queryKey: getEdgesListQueryKey() });
      onDone();
    } catch (error) {
      if (applyFieldErrors(error, form.setError, EDGE_FIELD_PATHS, t).length === 0) {
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
        <SheetBody className="grid content-start gap-4">
          {editing === null ? (
            <FormField
              control={form.control}
              name="edge_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("edges.form.edgeId")}</FormLabel>
                  <FormControl>
                    <Input
                      autoComplete="off"
                      spellCheck={false}
                      dir="ltr"
                      className="font-mono"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>{t("edges.form.edgeIdHelp")}</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          ) : null}
          <FormField
            control={form.control}
            name="name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("edges.form.name")}</FormLabel>
                <FormControl>
                  <Input autoComplete="off" dir="auto" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          {kind !== "local" ? (
            <FormField
              control={form.control}
              name="base_url"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("edges.form.baseUrl")}</FormLabel>
                  <FormControl>
                    <Input
                      autoComplete="off"
                      spellCheck={false}
                      dir="ltr"
                      className="font-mono"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    {kind === "cdn" ? t("edges.form.cdnUrlHelp") : t("edges.form.baseUrlHelp")}
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          ) : null}
          {kind === "standalone" ? (
            <FormField
              control={form.control}
              name="storage"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("edges.form.storage")}</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {STORAGES.map((storage) => (
                        <SelectItem key={storage} value={storage}>
                          {t(`edges.storage.${storage}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription>{t(`edges.storageHelp.${field.value}`)}</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          ) : null}
          {kind === "cdn" ? (
            <FormField
              control={form.control}
              name="origin_edge_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("edges.form.origin")}</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder={t("edges.form.originPlaceholder")} />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {origins.map((origin) => (
                        <SelectItem key={origin.id} value={origin.id}>
                          {origin.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription>{t("edges.form.originHelp")}</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          ) : null}
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              control={form.control}
              name="weight"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("edges.form.weight")}</FormLabel>
                  <FormControl>
                    <Input inputMode="numeric" dir="ltr" {...field} />
                  </FormControl>
                  <FormDescription>{t("edges.form.weightHelp")}</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="capacity_mbps"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("edges.form.capacity")}</FormLabel>
                  <FormControl>
                    <Input inputMode="numeric" dir="ltr" {...field} />
                  </FormControl>
                  <FormDescription>{t("edges.form.capacityHelp")}</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>
          <FormField
            control={form.control}
            name="audience"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("edges.form.audience")}</FormLabel>
                <Select value={field.value} onValueChange={field.onChange}>
                  <FormControl>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {AUDIENCES.map((choice) => (
                      <SelectItem key={choice} value={choice}>
                        {t(`edges.audience.${choice}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormDescription>{t("edges.form.audienceHelp")}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          {audience === "canary" ? (
            <FormField
              control={form.control}
              name="canary_user_ids"
              render={({ field }) => (
                <FormItem>
                  <FormLabel id={canaryLabel}>{t("edges.form.canary")}</FormLabel>
                  <CanaryPicker
                    value={field.value}
                    onChange={field.onChange}
                    labelledBy={canaryLabel}
                  />
                  <FormMessage />
                </FormItem>
              )}
            />
          ) : null}
          <FormField
            control={form.control}
            name="draining"
            render={({ field }) => (
              <FormItem className="flex items-center justify-between gap-4">
                <div className="grid gap-1">
                  <FormLabel>{t("edges.form.draining")}</FormLabel>
                  <FormDescription>{t("edges.form.drainingHelp")}</FormDescription>
                </div>
                <FormControl>
                  <Switch checked={field.value} onCheckedChange={field.onChange} />
                </FormControl>
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="enabled"
            render={({ field }) => (
              <FormItem className="flex items-center justify-between gap-4">
                <div className="grid gap-1">
                  <FormLabel>{t("edges.form.enabled")}</FormLabel>
                  <FormDescription>{t("edges.form.enabledHelp")}</FormDescription>
                </div>
                <FormControl>
                  <Switch checked={field.value} onCheckedChange={field.onChange} />
                </FormControl>
              </FormItem>
            )}
          />
        </SheetBody>
        <SheetFooter>
          <Button type="button" variant="secondary" onClick={onDone}>
            {t("edges.form.cancel")}
          </Button>
          <Button type="submit" pending={form.formState.isSubmitting}>
            {editing ? t("edges.form.save") : t("edges.form.add")}
          </Button>
        </SheetFooter>
      </form>
    </Form>
  );
}

export function EdgeSheet({
  target,
  origins,
  onClose,
  onCreated,
}: {
  target: EdgeSheetTarget | null;
  origins: Edge[];
  onClose: () => void;
  onCreated: (created: EdgeCreated) => void;
}) {
  const { t } = useTranslation();
  const editing = target !== null && "edge" in target ? target.edge : null;
  const kind =
    target === null ? "standalone" : editing ? editing.kind : (target as { kind: NewKind }).kind;
  return (
    <Sheet
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>
            {editing
              ? t("edges.form.editTitle", { name: editing.name })
              : t(`edges.form.addTitle.${kind}`)}
          </SheetTitle>
          <SheetDescription>{t(`edges.kindHelp.${kind}`)}</SheetDescription>
        </SheetHeader>
        {target !== null ? (
          <EdgeForm
            key={editing?.id ?? kind}
            target={target}
            origins={origins}
            onDone={onClose}
            onCreated={onCreated}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
