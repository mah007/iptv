import type { ImportStatus } from "@smart-iptv/api";
import {
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
  useFormatters,
} from "@smart-iptv/ui";
import { useFormContext, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

/** Where the files go and under which rights: shared by the browser and the link form. */
export const targetSchema = z.object({
  library: z.string().min(1, "imports.validation.libraryRequired"),
  subfolder: z.string().trim().max(500),
  rights_holder: z.string().trim().min(1, "imports.validation.rightsHolderRequired").max(255),
  license_ref: z.string().trim().min(1, "imports.validation.licenseRefRequired").max(255),
  license_expires_at: z.string(),
});

export type TargetValues = z.output<typeof targetSchema>;

export const TARGET_DEFAULTS: TargetValues = {
  library: "",
  subfolder: "",
  rights_holder: "",
  license_ref: "",
  license_expires_at: "",
};

/** The API fields of a target, as the form paths they fill. */
export const TARGET_FIELD_PATHS = {
  library: "library",
  subfolder: "subfolder",
  rights_holder: "rights_holder",
  license_ref: "license_ref",
  license_expires_at: "license_expires_at",
} as const;

export function targetBody(values: TargetValues) {
  return {
    library: values.library,
    subfolder: values.subfolder,
    rights_holder: values.rights_holder,
    license_ref: values.license_ref,
    // The licence ends with the day the admin picked (UTC).
    license_expires_at: values.license_expires_at ? `${values.license_expires_at}T23:59:59Z` : null,
  };
}

/** Library (with its free space), folder inside it, and the rights the files come under. */
export function TargetFields({ status }: { status: ImportStatus | undefined }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const form = useFormContext<TargetValues>();
  const chosen = useWatch({ control: form.control, name: "library" });
  const library = status?.libraries.find((item) => item.id === chosen);
  const free = library?.free_bytes ?? null;
  const total = library?.total_bytes ?? null;
  return (
    <>
      <FormField
        control={form.control}
        name="library"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("imports.form.library")}</FormLabel>
            <Select value={field.value} onValueChange={field.onChange}>
              <FormControl>
                <SelectTrigger>
                  <SelectValue placeholder={t("imports.form.libraryPlaceholder")} />
                </SelectTrigger>
              </FormControl>
              <SelectContent>
                {(status?.libraries ?? []).map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {free !== null && total !== null ? (
              <FormDescription>
                {t("imports.form.libraryFree", {
                  free: format.bytes(free),
                  total: format.bytes(total),
                  reserve: format.bytes(status?.reserve_bytes ?? 0),
                })}
              </FormDescription>
            ) : null}
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name="subfolder"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("imports.form.subfolder")}</FormLabel>
            <FormControl>
              <Input autoComplete="off" dir="auto" {...field} />
            </FormControl>
            <FormDescription>{t("imports.form.subfolderHelp")}</FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name="rights_holder"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("imports.form.rightsHolder")}</FormLabel>
            <FormControl>
              <Input autoComplete="off" dir="auto" {...field} />
            </FormControl>
            <FormDescription>{t("imports.form.rightsHolderHelp")}</FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name="license_ref"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("imports.form.licenseRef")}</FormLabel>
            <FormControl>
              <Input autoComplete="off" dir="auto" {...field} />
            </FormControl>
            <FormDescription>{t("imports.form.licenseRefHelp")}</FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name="license_expires_at"
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t("imports.form.licenseExpires")}</FormLabel>
            <FormControl>
              <Input type="date" dir="ltr" {...field} />
            </FormControl>
            <FormDescription>{t("imports.form.licenseExpiresHelp")}</FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
    </>
  );
}
