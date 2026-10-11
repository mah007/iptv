import {
  getIntegrationsListQueryKey,
  getSettingsListQueryKey,
  getSystemInfoQueryKey,
  useSettingsAdopt,
  useSettingsBulkUpdate,
  useSettingsReset,
  useSettingsUpdate,
  type SettingEntry,
} from "@smart-iptv/api";
import { useQueryClient } from "@tanstack/react-query";

type Value = boolean | number | string;

/**
 * Setting writes (ADR-0019): change, reset (back to `.env` or the default), move a
 * `.env` value in, several at once. Each keeps the settings list current and
 * refreshes the pages built from it (Integrations, System).
 */
export function useSettingActions() {
  const queryClient = useQueryClient();
  const update = useSettingsUpdate();
  const reset = useSettingsReset();
  const adopt = useSettingsAdopt();
  const bulk = useSettingsBulkUpdate();

  function apply(saved: readonly SettingEntry[]): void {
    const byKey = new Map(saved.map((entry) => [entry.key, entry]));
    queryClient.setQueryData(getSettingsListQueryKey(), (list: SettingEntry[] | undefined) =>
      list?.map((item) => byKey.get(item.key) ?? item),
    );
    void queryClient.invalidateQueries({ queryKey: getIntegrationsListQueryKey() });
    void queryClient.invalidateQueries({ queryKey: getSystemInfoQueryKey() });
  }

  return {
    pending: update.isPending || reset.isPending || adopt.isPending || bulk.isPending,
    set: (key: string, value: Value) =>
      update.mutateAsync({ key, data: { value } }).then((saved) => {
        apply([saved]);
        return saved;
      }),
    reset: (key: string) =>
      reset.mutateAsync({ key }).then((saved) => {
        apply([saved]);
        return saved;
      }),
    adopt: (key: string) =>
      adopt.mutateAsync({ key }).then((saved) => {
        apply([saved]);
        return saved;
      }),
    setMany: (values: Record<string, Value | null>) =>
      bulk.mutateAsync({ data: { values } }).then((saved) => {
        apply(saved);
        return saved;
      }),
  };
}
