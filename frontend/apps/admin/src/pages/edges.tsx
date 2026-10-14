import {
  getEdgesListQueryKey,
  useEdgesDelete,
  useEdgesList,
  useEdgesRotateSecret,
  useEdgesTest,
  type Edge,
  type EdgeCreated,
  type EdgeList,
  type ProbeResult,
} from "@smart-iptv/api";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  CopyField,
  DescriptionItem,
  DescriptionList,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  EmptyState,
  PageHeader,
  RelativeTime,
  SecretReveal,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  toast,
  useFormatters,
  type BadgeTone,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { CheckCircle2, MoreHorizontal, Network, Plus, XCircle } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError, RequirePermission } from "../components/states";
import { EdgeSheet, type EdgeSheetTarget } from "../features/edges/edge-sheet";
import { installCommand } from "../features/edges/schemas";
import { useCan } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";
import { notifyError } from "../lib/problems";

/** Edges report every 15 s; the page follows. */
const REFRESH_MS = 15_000;

function statusTone(status: string): BadgeTone {
  if (status === "healthy") return "success";
  if (status === "disabled" || status === "never_seen") return "neutral";
  if (status === "draining" || status === "stale" || status === "keys_outdated") return "warning";
  return "danger";
}

function StatusCell({ edge }: { edge: Edge }) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-0.5">
      <Badge tone={statusTone(edge.status)} dot>
        {t(`edges.status.${edge.status}`, { defaultValue: edge.status })}
      </Badge>
      {edge.last_seen_at ? (
        <span className="text-xs text-muted-foreground">
          <RelativeTime value={edge.last_seen_at} />
        </span>
      ) : null}
    </div>
  );
}

function LoadCell({ edge }: { edge: Edge }) {
  const { t } = useTranslation();
  const format = useFormatters();
  if (edge.kind === "cdn")
    return <span className="text-muted-foreground">{t("edges.cdnLoad")}</span>;
  return (
    <div className="grid gap-0.5 text-ui">
      <span>
        {edge.egress_mbps === null
          ? t("edges.noEgress")
          : t("edges.egress", {
              used: format.number(edge.egress_mbps),
              capacity: format.number(edge.capacity_mbps),
            })}
      </span>
      {edge.connections !== null ? (
        <span className="text-xs text-muted-foreground">
          {t("edges.connections", { count: edge.connections })}
        </span>
      ) : null}
    </div>
  );
}

function TestResultDialog({
  result,
  onClose,
}: {
  result: { edge: Edge; probe: ProbeResult } | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={result !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("edges.test.title", { name: result?.edge.name ?? "" })}</DialogTitle>
          <DialogDescription>
            {result?.probe.ok ? t("edges.test.passed") : t("edges.test.failed")}
          </DialogDescription>
        </DialogHeader>
        <ul className="grid gap-2">
          {(result?.probe.steps ?? []).map((step) => (
            <li key={step.name} className="flex items-start gap-2 text-ui">
              {step.ok ? (
                <CheckCircle2
                  aria-hidden="true"
                  className="mt-0.5 size-4 shrink-0 text-success-text"
                />
              ) : (
                <XCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-danger-text" />
              )}
              <span className="grid">
                <span className="font-medium">
                  {t(`edges.test.steps.${step.name}`, { defaultValue: step.name })}
                </span>
                <span className="text-muted-foreground">
                  {t(`edges.test.codes.${step.code}`, { defaultValue: step.code })}
                  {step.detail ? (
                    <span dir="ltr" className="ms-1 font-mono text-xs">
                      ({step.detail})
                    </span>
                  ) : null}
                </span>
              </span>
            </li>
          ))}
        </ul>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            {t("edges.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SecretDialog({
  shown,
  controlUrl,
  onClose,
}: {
  shown: { edge: Pick<Edge, "edge_id" | "base_url" | "name">; secret: string } | null;
  controlUrl: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={shown !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("edges.secret.title", { name: shown?.edge.name ?? "" })}</DialogTitle>
          <DialogDescription>{t("edges.secret.description")}</DialogDescription>
        </DialogHeader>
        {shown ? (
          <div className="grid gap-4">
            <SecretReveal label={t("edges.secret.label")} secret={shown.secret} />
            <CopyField
              label={t("edges.secret.command")}
              value={installCommand(controlUrl, shown.edge)}
              mono
            />
            <Alert tone="warning" title={t("edges.secret.onceTitle")}>
              {t("edges.secret.once")}
            </Alert>
          </div>
        ) : null}
        <DialogFooter>
          <Button onClick={onClose}>{t("edges.secret.done")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Facts({ data }: { data: EdgeList }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("edges.facts.title")}</CardTitle>
        <CardDescription>{t("edges.facts.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <DescriptionList>
          <DescriptionItem label={t("edges.facts.signingKey")}>
            <span dir="ltr" className="font-mono">
              {data.signing_kid || t("edges.facts.none")}
            </span>
          </DescriptionItem>
          <DescriptionItem label={t("edges.facts.controlUrl")}>
            <span dir="ltr" className="font-mono break-all">
              {data.control_url}
            </span>
          </DescriptionItem>
          <DescriptionItem label={t("edges.facts.s3")}>
            <Link to="/settings/integrations" className="underline-offset-4 hover:underline">
              {data.s3_enabled ? t("edges.facts.s3On") : t("edges.facts.s3Off")}
            </Link>
          </DescriptionItem>
          <DescriptionItem label={t("edges.facts.cdn")}>
            <Link to="/settings/integrations" className="underline-offset-4 hover:underline">
              {data.cdn_ready ? t("edges.facts.cdnOn") : t("edges.facts.cdnOff")}
            </Link>
          </DescriptionItem>
        </DescriptionList>
      </CardContent>
    </Card>
  );
}

function EdgesView() {
  const { t } = useTranslation();
  const can = useCan();
  const queryClient = useQueryClient();
  const query = useEdgesList({ query: { refetchInterval: REFRESH_MS } });
  const [sheet, setSheet] = useState<EdgeSheetTarget | null>(null);
  const [secret, setSecret] = useState<{
    edge: Pick<Edge, "edge_id" | "base_url" | "name">;
    secret: string;
  } | null>(null);
  const [probe, setProbe] = useState<{ edge: Edge; probe: ProbeResult } | null>(null);
  const [confirm, setConfirm] = useState<{ action: "rotate" | "remove"; edge: Edge } | null>(null);
  const rotate = useEdgesRotateSecret();
  const remove = useEdgesDelete();
  const test = useEdgesTest();
  const manage = can("edges.manage");
  const refresh = () => queryClient.invalidateQueries({ queryKey: getEdgesListQueryKey() });

  if (query.isPending) {
    return <Skeleton className="h-64" />;
  }
  if (query.isError) {
    return <QueryError error={query.error} onRetry={() => void query.refetch()} />;
  }
  const data = query.data;
  const origins = data.edges.filter((edge) => edge.kind !== "cdn");

  const runTest = (edge: Edge) => {
    test.mutate(
      { id: edge.id },
      {
        onSuccess: (result) => {
          setProbe({ edge, probe: result });
        },
        onError: (error) => {
          notifyError(t, error);
        },
      },
    );
  };
  const onCreated = (created: EdgeCreated) => {
    toast.success(t("edges.form.created", { name: created.name }));
    if (created.secret) setSecret({ edge: created, secret: created.secret });
  };

  return (
    // grid-cols-1: minmax(0, 1fr), so the table's own scroller takes its width, not the page.
    <div className="grid grid-cols-1 gap-6">
      <PageHeader
        title={t("edges.title")}
        description={t("edges.description")}
        actions={
          manage ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button>
                  <Plus aria-hidden="true" />
                  {t("edges.add")}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  onSelect={() => {
                    setSheet({ kind: "standalone" });
                  }}
                >
                  {t("edges.addStandalone")}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onSelect={() => {
                    setSheet({ kind: "cdn" });
                  }}
                >
                  {t("edges.addCdn")}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null
        }
      />
      <Facts data={data} />
      <Card>
        <CardContent className="p-0">
          {data.edges.length === 0 ? (
            <EmptyState icon={<Network aria-hidden="true" />} title={t("edges.empty")} />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("edges.columns.edge")}</TableHead>
                    <TableHead>{t("edges.columns.kind")}</TableHead>
                    <TableHead>{t("edges.columns.status")}</TableHead>
                    <TableHead>{t("edges.columns.load")}</TableHead>
                    <TableHead>{t("edges.columns.weight")}</TableHead>
                    <TableHead>
                      <span className="sr-only">{t("edges.columns.actions")}</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.edges.map((edge) => (
                    <TableRow key={edge.id} data-edge={edge.edge_id}>
                      <TableCell>
                        <div className="grid gap-0.5">
                          <span className="font-medium" dir="auto">
                            {edge.name}
                          </span>
                          <span className="text-xs text-muted-foreground" dir="ltr">
                            {edge.effective_url}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <div className="grid gap-0.5">
                          <span>{t(`edges.kind.${edge.kind}`)}</span>
                          <span className="text-xs text-muted-foreground">
                            {edge.kind === "cdn"
                              ? t("edges.pullsFrom", {
                                  name:
                                    origins.find((o) => o.id === edge.origin_edge_id)?.name ?? "",
                                })
                              : t(`edges.storage.${edge.storage}`)}
                          </span>
                          {edge.audience === "canary" ? (
                            <Badge tone="info">
                              {t("edges.canaryBadge", { count: edge.canary_user_ids.length })}
                            </Badge>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell>
                        <StatusCell edge={edge} />
                      </TableCell>
                      <TableCell>
                        <LoadCell edge={edge} />
                      </TableCell>
                      <TableCell dir="ltr">{edge.weight}</TableCell>
                      <TableCell className="text-end">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={t("edges.actionsFor", { name: edge.name })}
                            >
                              <MoreHorizontal aria-hidden="true" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem
                              disabled={!manage || test.isPending}
                              onSelect={() => {
                                runTest(edge);
                              }}
                            >
                              {t("edges.actions.test")}
                            </DropdownMenuItem>
                            {manage ? (
                              <DropdownMenuItem
                                onSelect={() => {
                                  setSheet({ edge });
                                }}
                              >
                                {t("edges.actions.edit")}
                              </DropdownMenuItem>
                            ) : null}
                            {manage && edge.kind === "standalone" ? (
                              <DropdownMenuItem
                                onSelect={() => {
                                  setConfirm({ action: "rotate", edge });
                                }}
                              >
                                {t("edges.actions.rotate")}
                              </DropdownMenuItem>
                            ) : null}
                            {manage && edge.kind !== "local" ? (
                              <DropdownMenuItem
                                className="text-danger-text"
                                onSelect={() => {
                                  setConfirm({ action: "remove", edge });
                                }}
                              >
                                {t("edges.actions.remove")}
                              </DropdownMenuItem>
                            ) : null}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
      <EdgeSheet
        target={sheet}
        origins={origins}
        onClose={() => {
          setSheet(null);
        }}
        onCreated={onCreated}
      />
      <SecretDialog
        shown={secret}
        controlUrl={data.control_url}
        onClose={() => {
          setSecret(null);
        }}
      />
      <TestResultDialog
        result={probe}
        onClose={() => {
          setProbe(null);
        }}
      />
      <ConfirmDialog
        open={confirm !== null}
        onOpenChange={(open) => {
          if (!open) setConfirm(null);
        }}
        title={
          confirm?.action === "rotate"
            ? t("edges.confirm.rotateTitle", { name: confirm.edge.name })
            : t("edges.confirm.removeTitle", { name: confirm?.edge.name ?? "" })
        }
        description={
          confirm?.action === "rotate" ? t("edges.confirm.rotate") : t("edges.confirm.remove")
        }
        confirmLabel={
          confirm?.action === "rotate" ? t("edges.actions.rotate") : t("edges.actions.remove")
        }
        tone="danger"
        onConfirm={async () => {
          if (confirm === null) return;
          const { action, edge } = confirm;
          try {
            if (action === "rotate") {
              const result = await rotate.mutateAsync({ id: edge.id });
              setSecret({ edge, secret: result.secret });
            } else {
              await remove.mutateAsync({ id: edge.id });
              toast.success(t("edges.removed", { name: edge.name }));
            }
            await refresh();
          } catch (error) {
            notifyError(t, error);
          } finally {
            setConfirm(null);
          }
        }}
      />
    </div>
  );
}

export function EdgesPage() {
  const { t } = useTranslation();
  usePageTitle(t("edges.title"));
  return (
    <RequirePermission permission="edges.view">
      <EdgesView />
    </RequirePermission>
  );
}
