import { zodResolver } from "@hookform/resolvers/zod";
import {
  AccessRuleType,
  getAccessRulesListQueryKey,
  useAccessRulesCreate,
  useAccessRulesDestroy,
  useAccessRulesList,
  type AccessRule,
  type AccessRulesListParams,
} from "@smart-iptv/api";
import {
  Badge,
  Button,
  ConfirmDialog,
  CountryFlag,
  DEFAULT_TIME_ZONE,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  RelativeTime,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  toast,
  useFormatters,
  useNow,
  type BadgeTone,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Plus, ShieldBan, Trash2 } from "lucide-react";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { QueryError } from "../../components/states";
import { useCan, useMe } from "../../lib/auth";
import { applyFieldErrors, notifyError } from "../../lib/problems";
import { endOfDayIn, isoDateIn } from "../../lib/time";

export const RULE_TYPES = Object.values(AccessRuleType);

const TYPE_TONE: Record<AccessRuleType, BadgeTone> = {
  ip_allow: "success",
  country_allow: "success",
  ip_deny: "danger",
  cidr_deny: "danger",
  country_deny: "danger",
};

const IPV4 = /^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$/u;
const IPV6 = /^[0-9a-f:]+$/iu;

/** A first check of the value for its type; the API normalises and has the last word. */
function valueProblem(type: AccessRuleType, value: string): string | null {
  const trimmed = value.trim();
  if (trimmed === "") return "accessRules.validation.value";
  if (type === "country_allow" || type === "country_deny") {
    return /^[a-z]{2}$/iu.test(trimmed) ? null : "fieldErrors.invalid_country";
  }
  if (type === "cidr_deny") {
    const [address, bits, ...rest] = trimmed.split("/");
    const valid =
      rest.length === 0 &&
      address !== undefined &&
      bits !== undefined &&
      /^\d{1,3}$/u.test(bits) &&
      (IPV4.test(address) || (address.includes(":") && IPV6.test(address)));
    return valid ? null : "fieldErrors.invalid_cidr";
  }
  return IPV4.test(trimmed) || (trimmed.includes(":") && IPV6.test(trimmed))
    ? null
    : "fieldErrors.invalid_ip";
}

const ruleSchema = z
  .object({
    type: z.enum(AccessRuleType),
    value: z.string(),
    reason: z.string().max(200, "accessRules.validation.reason"),
    expires: z.string(),
  })
  .superRefine((values, context) => {
    const problem = valueProblem(values.type, values.value);
    if (problem !== null) context.addIssue({ code: "custom", path: ["value"], message: problem });
    if (values.expires !== "" && !/^\d{4}-\d{2}-\d{2}$/u.test(values.expires)) {
      context.addIssue({
        code: "custom",
        path: ["expires"],
        message: "accessRules.validation.expires",
      });
    }
  });

type RuleForm = z.infer<typeof ruleSchema>;

const FIELD_PATHS = {
  type: "type",
  value: "value",
  reason: "reason",
  expires_at: "expires",
} as const;

function ruleValueText(rule: AccessRule) {
  if (rule.type === "country_allow" || rule.type === "country_deny") {
    return <CountryFlag code={rule.value} showName />;
  }
  return (
    <code className="font-mono text-xs" dir="ltr">
      {rule.value}
    </code>
  );
}

export function AddRuleDialog({
  open,
  onOpenChange,
  user,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The customer the rule is for; null for a rule that applies to everyone. */
  user: string | null;
}) {
  const { t } = useTranslation();
  const me = useMe();
  const timeZone = me?.timezone ?? DEFAULT_TIME_ZONE;
  const queryClient = useQueryClient();
  const create = useAccessRulesCreate();
  const form = useForm<RuleForm>({
    resolver: zodResolver(ruleSchema),
    defaultValues: { type: "ip_deny", value: "", reason: "", expires: "" },
  });
  const type = useWatch({ control: form.control, name: "type" });
  const today = isoDateIn(useNow(60_000), timeZone);

  const submit = form.handleSubmit(async (values) => {
    try {
      await create.mutateAsync({
        data: {
          user,
          type: values.type,
          value: values.value.trim(),
          reason: values.reason.trim(),
          expires_at: values.expires ? endOfDayIn(values.expires, timeZone) : null,
        },
      });
      await queryClient.invalidateQueries({ queryKey: getAccessRulesListQueryKey() });
      toast.success(t("accessRules.added"));
      form.reset();
      onOpenChange(false);
    } catch (error) {
      if (applyFieldErrors(error, form.setError, FIELD_PATHS, t).length === 0) {
        notifyError(t, error);
      }
    }
  });

  const valueHelp =
    type === "country_allow" || type === "country_deny"
      ? t("accessRules.help.country")
      : type === "cidr_deny"
        ? t("accessRules.help.cidr")
        : t("accessRules.help.ip");

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
          <DialogTitle>
            {user === null ? t("accessRules.add.globalTitle") : t("accessRules.add.customerTitle")}
          </DialogTitle>
          <DialogDescription>{t("accessRules.add.description")}</DialogDescription>
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
              name="type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("accessRules.fields.type")}</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {RULE_TYPES.map((ruleType) => (
                        <SelectItem key={ruleType} value={ruleType}>
                          {t(`accessRules.types.${ruleType}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="value"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("accessRules.fields.value")}</FormLabel>
                  <FormControl>
                    <Input
                      dir="ltr"
                      autoComplete="off"
                      spellCheck={false}
                      className="font-mono"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>{valueHelp}</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="reason"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("accessRules.fields.reason")}</FormLabel>
                  <FormControl>
                    <Input dir="auto" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="expires"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("accessRules.fields.expires")}</FormLabel>
                  <FormControl>
                    <Input type="date" min={today} className="w-fit" {...field} />
                  </FormControl>
                  <FormDescription>{t("accessRules.help.expires")}</FormDescription>
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
                {t("accessRules.add.submit")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

/**
 * IP, network and country rules (SPEC §8.3.15, §7.4 check 4): everyone's when
 * `user` is null, else one customer's. Rules take effect at the next playback
 * check; removing one is audited.
 */
export function AccessRulesTable({
  params,
  showCustomer = false,
  emptyTitle,
}: {
  params: AccessRulesListParams;
  showCustomer?: boolean;
  emptyTitle: string;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const can = useCan();
  const queryClient = useQueryClient();
  const query = useAccessRulesList({ page_size: 100, ...params });
  const destroy = useAccessRulesDestroy();
  const now = useNow(60_000);
  const [removing, setRemoving] = useState<AccessRule | null>(null);
  const editable = can("customers.edit");

  if (query.isPending) {
    return (
      <div className="grid gap-2 p-4" role="status" aria-live="polite">
        <span className="sr-only">{t("layout.loading")}</span>
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
      </div>
    );
  }
  if (query.isError) {
    return (
      <QueryError
        className="py-8"
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  const rules = query.data.results;
  if (rules.length === 0) {
    return (
      <EmptyState
        className="py-8"
        icon={<ShieldBan />}
        title={emptyTitle}
        description={t("accessRules.empty.description")}
      />
    );
  }
  return (
    <>
      <div className="overflow-x-auto">
        <Table aria-label={t("accessRules.title")}>
          <TableHeader>
            <TableRow>
              <TableHead>{t("accessRules.columns.type")}</TableHead>
              <TableHead>{t("accessRules.columns.value")}</TableHead>
              {showCustomer ? <TableHead>{t("accessRules.columns.customer")}</TableHead> : null}
              <TableHead>{t("accessRules.columns.reason")}</TableHead>
              <TableHead>{t("accessRules.columns.expires")}</TableHead>
              <TableHead>{t("accessRules.columns.added")}</TableHead>
              {editable ? (
                <TableHead>
                  <span className="sr-only">{t("accessRules.columns.actions")}</span>
                </TableHead>
              ) : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rules.map((rule) => {
              const expired = rule.expires_at !== null && Date.parse(rule.expires_at) <= now;
              return (
                <TableRow key={rule.id} data-state={expired ? "expired" : undefined}>
                  <TableCell>
                    <Badge tone={expired ? "neutral" : TYPE_TONE[rule.type]}>
                      {t(`accessRules.types.${rule.type}`)}
                    </Badge>
                  </TableCell>
                  <TableCell>{ruleValueText(rule)}</TableCell>
                  {showCustomer ? (
                    <TableCell>
                      {rule.user ? (
                        <Link
                          to="/customers/$customerId"
                          params={{ customerId: rule.user }}
                          search={{ tab: "security" }}
                          className="text-ui underline-offset-2 outline-none hover:underline focus-visible:underline"
                        >
                          {t("accessRules.openCustomer")}
                        </Link>
                      ) : null}
                    </TableCell>
                  ) : null}
                  <TableCell className="max-w-64">
                    <span className="block truncate" dir="auto">
                      {rule.reason}
                    </span>
                  </TableCell>
                  <TableCell>
                    {rule.expires_at === null ? (
                      <span className="text-muted-foreground">{t("accessRules.never")}</span>
                    ) : expired ? (
                      <Badge>{t("accessRules.expired")}</Badge>
                    ) : (
                      <span title={format.dateTime(rule.expires_at)}>
                        <RelativeTime value={rule.expires_at} />
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <RelativeTime value={rule.created_at} className="text-muted-foreground" />
                  </TableCell>
                  {editable ? (
                    <TableCell className="text-end">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={t("accessRules.remove.action", { value: rule.value })}
                        onClick={() => {
                          setRemoving(rule);
                        }}
                      >
                        <Trash2 aria-hidden="true" />
                      </Button>
                    </TableCell>
                  ) : null}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        tone="danger"
        title={t("accessRules.remove.title")}
        description={t("accessRules.remove.description", {
          type: removing ? t(`accessRules.types.${removing.type}`) : "",
          value: removing?.value ?? "",
        })}
        confirmLabel={t("accessRules.remove.confirm")}
        onConfirm={async () => {
          if (removing === null) return;
          try {
            await destroy.mutateAsync({ id: removing.id });
            await queryClient.invalidateQueries({ queryKey: getAccessRulesListQueryKey() });
            toast.success(t("accessRules.removed"));
          } catch (error) {
            notifyError(t, error);
            throw error;
          }
        }}
      />
    </>
  );
}

/** The add button for a rules list. */
export function AddRuleButton({ user }: { user: string | null }) {
  const { t } = useTranslation();
  const can = useCan();
  const [open, setOpen] = useState(false);
  if (!can("customers.edit")) return null;
  return (
    <>
      <Button
        onClick={() => {
          setOpen(true);
        }}
      >
        <Plus aria-hidden="true" />
        {t("accessRules.add.button")}
      </Button>
      <AddRuleDialog open={open} onOpenChange={setOpen} user={user} />
    </>
  );
}
