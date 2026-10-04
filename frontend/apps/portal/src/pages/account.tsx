import { zodResolver } from "@hookform/resolvers/zod";
import {
  getMeRetrieveQueryKey,
  useAuthPasswordForgot,
  useMeUpdate,
  type CustomerMe,
} from "@smart-iptv/api-portal";
import {
  Alert,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
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
  Switch,
  toast,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  ChevronRight,
  CreditCard,
  History,
  KeyRound,
  LogOut,
  MonitorSmartphone,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { useSignOut } from "../layout/account-menu";
import { useMe } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";

const profileSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "account.validation.nameRequired")
    .max(150, "auth.validation.tooLong"),
  locale: z.enum(["ar", "en"]),
  timezone: z.string().min(1).max(64),
  marketing_opt_in: z.boolean(),
});
type ProfileValues = z.output<typeof profileSchema>;

/** Every IANA zone the browser knows, the customer's own first if it is unusual. */
function timeZones(current: string): string[] {
  const zones =
    typeof Intl.supportedValuesOf === "function"
      ? Intl.supportedValuesOf("timeZone")
      : ["Asia/Riyadh", "UTC"];
  return zones.includes(current) ? zones : [current, ...zones];
}

function ProfileForm({ me }: { me: CustomerMe }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const update = useMeUpdate();
  const zones = useMemo(() => timeZones(me.timezone), [me.timezone]);
  const form = useForm<ProfileValues>({
    resolver: zodResolver(profileSchema),
    defaultValues: {
      name: me.name,
      locale: me.locale,
      timezone: me.timezone,
      marketing_opt_in: me.marketing_opt_in,
    },
  });

  async function onSubmit(values: ProfileValues): Promise<void> {
    try {
      const saved = await update.mutateAsync({ data: values });
      queryClient.setQueryData(getMeRetrieveQueryKey(), saved);
      form.reset({
        name: saved.name,
        locale: saved.locale,
        timezone: saved.timezone,
        marketing_opt_in: saved.marketing_opt_in,
      });
      // The saved language is also the one this browser shows.
      if (saved.locale !== i18n.language) void i18n.changeLanguage(saved.locale);
      toast.success(t("account.saved"));
    } catch {
      toast.error(t("account.saveFailed"));
    }
  }

  return (
    <Form {...form}>
      <form
        noValidate
        className="grid gap-5"
        onSubmit={(event) => void form.handleSubmit(onSubmit)(event)}
      >
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("account.name")}</FormLabel>
              <FormControl>
                <Input autoComplete="name" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <div className="grid gap-5 sm:grid-cols-2">
          <FormField
            control={form.control}
            name="locale"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("account.language")}</FormLabel>
                <Select value={field.value} onValueChange={field.onChange}>
                  <FormControl>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    <SelectItem value="ar">
                      <span lang="ar">{t("account.languages.ar")}</span>
                    </SelectItem>
                    <SelectItem value="en">
                      <span lang="en">{t("account.languages.en")}</span>
                    </SelectItem>
                  </SelectContent>
                </Select>
                <FormDescription>{t("account.languageHint")}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="timezone"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("account.timezone")}</FormLabel>
                <Select value={field.value} onValueChange={field.onChange}>
                  <FormControl>
                    <SelectTrigger dir="ltr">
                      <SelectValue />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent className="max-h-72">
                    {zones.map((zone) => (
                      <SelectItem key={zone} value={zone} dir="ltr">
                        {zone}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        <FormField
          control={form.control}
          name="marketing_opt_in"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between gap-4 rounded-card border border-border p-4">
              <div className="grid gap-1">
                <FormLabel>{t("account.marketing")}</FormLabel>
                <FormDescription>{t("account.marketingHint")}</FormDescription>
              </div>
              <FormControl>
                <Switch checked={field.value} onCheckedChange={field.onChange} />
              </FormControl>
            </FormItem>
          )}
        />
        <div className="flex justify-end">
          <Button
            type="submit"
            pending={form.formState.isSubmitting}
            disabled={!form.formState.isDirty}
          >
            {t("account.save")}
          </Button>
        </div>
      </form>
    </Form>
  );
}

/** No password change endpoint exists: the customer gets a reset link by email instead. */
function PasswordCard({ me }: { me: CustomerMe }) {
  const { t } = useTranslation();
  const forgot = useAuthPasswordForgot();
  const [sent, setSent] = useState(false);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound aria-hidden="true" className="size-4" />
          {t("account.password")}
        </CardTitle>
        <CardDescription>
          {me.email ? t("account.passwordHint") : t("account.passwordNoEmail")}
        </CardDescription>
      </CardHeader>
      {me.email ? (
        <CardContent className="grid gap-3">
          {sent ? (
            <Alert tone="success" role="status">
              {t("account.passwordSent", { email: me.email })}
            </Alert>
          ) : null}
          <Button
            variant="secondary"
            className="justify-self-start"
            pending={forgot.isPending}
            disabled={sent}
            onClick={() => {
              forgot.mutate(
                { data: { login: me.username } },
                {
                  onSuccess: () => {
                    setSent(true);
                  },
                  onError: () => {
                    toast.error(t("account.passwordFailed"));
                  },
                },
              );
            }}
          >
            {t("account.passwordSend")}
          </Button>
        </CardContent>
      ) : null}
    </Card>
  );
}

function AccountLinks() {
  const { t } = useTranslation();
  const { signOut, pending } = useSignOut();
  const links = [
    {
      to: "/account/subscription",
      icon: CreditCard,
      label: t("nav.subscription"),
      hint: t("account.links.subscription"),
    },
    {
      to: "/account/devices",
      icon: MonitorSmartphone,
      label: t("nav.devices"),
      hint: t("account.links.devices"),
    },
    { to: "/history", icon: History, label: t("nav.history"), hint: t("account.links.history") },
  ] as const;
  return (
    <nav aria-label={t("account.sections")} className="grid gap-2">
      {links.map((link) => {
        const Icon = link.icon;
        return (
          <Link
            key={link.to}
            to={link.to}
            className="flex items-center gap-3 rounded-card border border-border bg-card p-4 outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Icon aria-hidden="true" className="size-5 text-primary" />
            <span className="grid min-w-0 flex-1">
              <span className="text-sm font-medium text-foreground">{link.label}</span>
              <span className="truncate text-xs text-muted-foreground">{link.hint}</span>
            </span>
            <ChevronRight
              aria-hidden="true"
              className="size-4 text-muted-foreground rtl:-scale-x-100"
            />
          </Link>
        );
      })}
      <Button variant="ghost" className="justify-start" pending={pending} onClick={signOut}>
        <LogOut aria-hidden="true" className="rtl:-scale-x-100" />
        {t("nav.signOut")}
      </Button>
    </nav>
  );
}

/** Profile: name, language, time zone, marketing email; password by email link. */
export function AccountPage() {
  const { t } = useTranslation();
  usePageTitle(t("account.title"));
  const me = useMe();
  if (me === undefined) return null;
  return (
    <div className="page-top mx-auto grid max-w-5xl gap-6 px-4 sm:px-6">
      <div className="grid gap-1">
        <h1 className="text-2xl font-semibold text-foreground sm:text-3xl">{t("account.title")}</h1>
        <p className="text-sm text-muted-foreground" dir="auto">
          {t("account.signedInAs", { name: me.username })}
        </p>
      </div>
      <div className="grid items-start gap-6 lg:grid-cols-[1fr_20rem]">
        <div className="grid gap-6">
          <Card>
            <CardHeader>
              <CardTitle>{t("account.profile")}</CardTitle>
              <CardDescription>{t("account.profileHint")}</CardDescription>
            </CardHeader>
            <CardContent>
              <ProfileForm me={me} />
            </CardContent>
          </Card>
          <PasswordCard me={me} />
        </div>
        <AccountLinks />
      </div>
    </div>
  );
}
