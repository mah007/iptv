import { useLiveSourceTestResult, type Probe } from "@smart-iptv/api";
import { Alert, Badge, useFormatters } from "@smart-iptv/ui";
import { useTranslation } from "react-i18next";

/** Poll a queued source test (`request_id`) until the worker answers. */
export function useSourceTest(requestId: string | null) {
  return useLiveSourceTestResult(requestId ?? "", {
    query: {
      enabled: requestId !== null,
      refetchInterval: (query) => (query.state.data?.status === "done" ? false : 1000),
    },
  });
}

function field(value: Record<string, unknown> | undefined, name: string): string {
  const raw = value?.[name];
  return typeof raw === "string" || typeof raw === "number" ? String(raw) : "";
}

/** What a source test found: codecs, size, bitrate and whether copying works. */
export function ProbeResult({ probe }: { probe: Probe }) {
  const { t } = useTranslation();
  const format = useFormatters();
  if (!probe.ok) {
    const code = probe.error ?? "unreachable";
    return (
      <Alert tone="danger" title={t("liveTv.test.failed")}>
        {t(`liveTv.test.errors.${code}`, { defaultValue: t("liveTv.test.errors.unreachable") })}
        {probe.detail ? (
          <span className="mt-1 block font-mono text-xs" dir="ltr">
            {probe.detail}
          </span>
        ) : null}
      </Alert>
    );
  }
  const video = probe.video;
  const audio = probe.audio;
  return (
    <Alert tone={probe.copy_ok ? "success" : "warning"} title={t("liveTv.test.ok")}>
      <span className="flex flex-wrap items-center gap-1.5">
        <Badge tone="neutral">
          <bdi dir="ltr">
            {field(video, "codec")} {field(video, "width")}×{field(video, "height")}
          </bdi>
        </Badge>
        {field(audio, "codec") ? (
          <Badge tone="neutral">
            <bdi dir="ltr">{field(audio, "codec")}</bdi>
          </Badge>
        ) : null}
        {probe.bitrate_kbps ? (
          <Badge tone="neutral">{format.bitrate((probe.bitrate_kbps ?? 0) * 1000)}</Badge>
        ) : null}
      </span>
      <span className="mt-1 block">
        {probe.copy_ok ? t("liveTv.test.copyOk") : t("liveTv.test.copyNo")}
      </span>
    </Alert>
  );
}
