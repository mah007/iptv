import type { TFunction } from "i18next";

/** The seed roles have translated names; roles admins create keep their own. */
export function roleLabel(t: TFunction, name: string): string {
  return t(`roles.names.${name}`, { defaultValue: name });
}

/** Permission codes ("customers.edit") read as sentences; new codes fall back to the API's text. */
export function permissionLabel(t: TFunction, code: string, description: string): string {
  return t(`roles.permissions.${code.replace(/\./gu, "_")}`, { defaultValue: description || code });
}

/** "customers.edit" → "customers": rows of the permission matrix are grouped by area. */
export function permissionArea(code: string): string {
  return code.split(".")[0] ?? code;
}
