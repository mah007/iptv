import {
  useLiveChannelsGuide,
  useLiveChannelsList,
  type LiveChannel,
  type Programme,
} from "@smart-iptv/api-portal";
import {
  Badge,
  Button,
  cn,
  EmptyState,
  ErrorState,
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  Skeleton,
  useFormatters,
} from "@smart-iptv/ui";
import { getRouteApi, Link } from "@tanstack/react-router";
import { CalendarClock, History, Play, Radio, Tv } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useMe } from "../lib/auth";
import { usePageTitle } from "../lib/page-title";

const liveApi = getRouteApi("/app/shell/live");

/** The listing refreshes so "now" and "next" follow the clock. */
const REFRESH_MS = 60_000;

/** A minute-precision clock, so progress bars move without refetching. */
function useNow(stepMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, stepMs);
    return () => {
      window.clearInterval(timer);
    };
  }, [stepMs]);
  return now;
}

/** Where a programme is, from 0 (starting) to 1 (ending). */
export function elapsedRatio(programme: Programme, now: number): number {
  const start = Date.parse(programme.start);
  const stop = Date.parse(programme.stop);
  if (!(stop > start)) return 0;
  return Math.min(1, Math.max(0, (now - start) / (stop - start)));
}

/** The catch-up window of a recorded programme, in whole minutes (at least one). */
export function catchupMinutes(programme: Programme): number {
  return Math.max(
    1,
    Math.ceil((Date.parse(programme.stop) - Date.parse(programme.start)) / 60_000),
  );
}

function ChannelLogo({ channel }: { channel: LiveChannel }) {
  return channel.logo_url ? (
    <img
      src={channel.logo_url}
      alt=""
      loading="lazy"
      className="size-12 shrink-0 rounded-input bg-muted object-contain p-1"
    />
  ) : (
    <span
      aria-hidden="true"
      className="grid size-12 shrink-0 place-items-center rounded-input bg-muted text-muted-foreground"
    >
      <Tv className="size-5" />
    </span>
  );
}

function TimeRange({ programme }: { programme: Programme }) {
  const format = useFormatters();
  const time = { dateStyle: undefined, timeStyle: "short" } as const;
  return (
    <span className="tabular-nums" dir="ltr">
      {format.dateTime(programme.start, time)}–{format.dateTime(programme.stop, time)}
    </span>
  );
}

function NowAndNext({ channel, now }: { channel: LiveChannel; now: number }) {
  const { t } = useTranslation();
  const format = useFormatters();
  if (!channel.now && !channel.next) {
    return <p className="text-sm text-muted-foreground">{t("live.noGuide")}</p>;
  }
  return (
    <div className="grid gap-1.5 text-sm">
      {channel.now ? (
        <div className="grid gap-1">
          <p className="flex min-w-0 items-baseline gap-2">
            <span className="shrink-0 text-xs font-semibold text-primary uppercase">
              {t("live.now")}
            </span>
            <span className="truncate font-medium" dir="auto">
              {channel.now.title || t("live.untitled")}
            </span>
          </p>
          <div aria-hidden="true" className="h-1 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary"
              style={{ width: `${String(elapsedRatio(channel.now, now) * 100)}%` }}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            <TimeRange programme={channel.now} />
          </p>
        </div>
      ) : null}
      {channel.next ? (
        <p className="flex min-w-0 items-baseline gap-2 text-muted-foreground">
          <span className="shrink-0 text-xs font-semibold uppercase">
            {t("live.nextAt", {
              time: format.dateTime(channel.next.start, {
                dateStyle: undefined,
                timeStyle: "short",
              }),
            })}
          </span>
          <span className="truncate" dir="auto">
            {channel.next.title || t("live.untitled")}
          </span>
        </p>
      ) : null}
    </div>
  );
}

function ChannelCard({
  channel,
  now,
  onGuide,
}: {
  channel: LiveChannel;
  now: number;
  onGuide: () => void;
}) {
  const { t } = useTranslation();
  return (
    <li className="grid content-start gap-3 rounded-card border border-border bg-card p-4 text-card-foreground">
      <div className="flex min-w-0 items-center gap-3">
        <ChannelLogo channel={channel} />
        <div className="grid min-w-0">
          <h2 className="truncate font-semibold" dir="auto">
            {channel.name}
          </h2>
          <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="tabular-nums">{t("live.number", { number: channel.number })}</span>
            {channel.catchup_days > 0 ? (
              <Badge tone="neutral">{t("live.catchupDays", { count: channel.catchup_days })}</Badge>
            ) : null}
          </p>
        </div>
      </div>
      <NowAndNext channel={channel} now={now} />
      <div className="flex flex-wrap items-center gap-2">
        {channel.web_playable ? (
          <Button asChild size="sm">
            <Link
              to="/watch/live/$channelId"
              params={{ channelId: channel.id }}
              aria-label={t("live.watchChannel", { name: channel.name })}
            >
              <Play aria-hidden="true" className="fill-current" />
              {t("live.watch")}
            </Link>
          </Button>
        ) : (
          <p className="text-xs text-muted-foreground">{t("live.tvAppOnly")}</p>
        )}
        <Button
          size="sm"
          variant="secondary"
          aria-label={t("live.guideFor", { name: channel.name })}
          onClick={onGuide}
        >
          <CalendarClock aria-hidden="true" />
          {t("live.guide")}
        </Button>
      </div>
    </li>
  );
}

/** One channel's guide: around now, with "watch from the start" on recorded programmes. */
function GuideDrawer({ channel, onClose }: { channel: LiveChannel | null; onClose: () => void }) {
  const { t } = useTranslation();
  const format = useFormatters();
  const now = useNow();
  const guide = useLiveChannelsGuide(channel?.id ?? "", undefined, {
    query: { enabled: channel !== null },
  });
  const programmes = guide.data?.programmes ?? [];
  const playable = channel?.web_playable === true;
  const days = programmes.map((programme) => format.date(programme.start, { dateStyle: "full" }));
  return (
    <Sheet
      open={channel !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent>
        <SheetHeader>
          <SheetTitle dir="auto">{channel?.name ?? ""}</SheetTitle>
          <SheetDescription>{t("live.guideDescription")}</SheetDescription>
        </SheetHeader>
        <SheetBody>
          {guide.isError ? (
            <ErrorState
              error={guide.error}
              onRetry={() => {
                void guide.refetch();
              }}
            />
          ) : guide.isPending ? (
            <div className="grid gap-3" aria-busy="true">
              {[0, 1, 2, 3].map((index) => (
                <Skeleton key={index} className="h-14" />
              ))}
            </div>
          ) : programmes.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("live.noGuide")}</p>
          ) : (
            <ol className="grid gap-2" aria-label={t("live.programmes")}>
              {programmes.map((programme, index) => {
                const onNow =
                  Date.parse(programme.start) <= now && Date.parse(programme.stop) > now;
                const heading = index === 0 || days[index] !== days[index - 1] ? days[index] : null;
                return (
                  <li key={programme.id} className="grid gap-2">
                    {heading ? (
                      <h3 className="pt-2 text-xs font-semibold text-muted-foreground uppercase">
                        {heading}
                      </h3>
                    ) : null}
                    <div
                      className={cn(
                        "grid gap-1 rounded-input border border-border p-3",
                        onNow && "border-primary",
                      )}
                    >
                      <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <TimeRange programme={programme} />
                        {onNow ? <Badge tone="danger">{t("live.onNow")}</Badge> : null}
                        {programme.catchup ? (
                          <Badge tone="neutral">{t("live.recorded")}</Badge>
                        ) : null}
                      </p>
                      <p className="font-medium" dir="auto">
                        {programme.title || t("live.untitled")}
                      </p>
                      {programme.description ? (
                        <p className="line-clamp-3 text-sm text-muted-foreground" dir="auto">
                          {programme.description}
                        </p>
                      ) : null}
                      {playable && (onNow || programme.catchup) ? (
                        <div className="pt-1">
                          <Button asChild size="sm" variant={onNow ? "primary" : "secondary"}>
                            {onNow ? (
                              <Link to="/watch/live/$channelId" params={{ channelId: channel.id }}>
                                <Radio aria-hidden="true" />
                                {t("live.watchLive")}
                              </Link>
                            ) : (
                              <Link
                                to="/watch/live/$channelId"
                                params={{ channelId: channel.id }}
                                search={{
                                  start: programme.start,
                                  duration: catchupMinutes(programme),
                                }}
                                aria-label={t("live.watchFromStartOf", {
                                  title: programme.title || t("live.untitled"),
                                })}
                              >
                                <History aria-hidden="true" />
                                {t("live.watchFromStart")}
                              </Link>
                            )}
                          </Button>
                        </div>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}

/** Live TV (ADR-0023): the plan's live groups, each channel with now and next and its guide. */
export function LivePage() {
  const { t } = useTranslation();
  usePageTitle(t("live.title"));
  const me = useMe();
  const { group } = liveApi.useSearch();
  const navigate = liveApi.useNavigate();
  const now = useNow();
  const listing = useLiveChannelsList({ query: { refetchInterval: REFRESH_MS } });
  const [guideFor, setGuideFor] = useState<LiveChannel | null>(null);
  const groups = listing.data?.groups ?? [];
  const selected = groups.some((item) => item.id === group) ? group : undefined;
  const channels = (listing.data?.channels ?? []).filter(
    (channel) => selected === undefined || channel.group_id === selected,
  );
  const allowed = me?.access?.allow_live !== false;

  return (
    <div className="page-top mx-auto grid max-w-[1800px] gap-6 px-4 sm:px-6 lg:px-10">
      <h1 className="text-2xl font-semibold text-foreground sm:text-3xl">{t("live.title")}</h1>
      {!allowed ? (
        <EmptyState
          icon={<Radio />}
          title={t("live.notInPlanTitle")}
          description={t("live.notInPlan")}
          action={
            <Button asChild>
              <Link to="/plans">{t("live.seePlans")}</Link>
            </Button>
          }
        />
      ) : listing.isError ? (
        <ErrorState
          error={listing.error}
          onRetry={() => {
            void listing.refetch();
          }}
        />
      ) : listing.isPending ? (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4" aria-busy="true">
          {[0, 1, 2, 3, 4, 5].map((index) => (
            <li key={index}>
              <Skeleton className="h-44 rounded-card" />
            </li>
          ))}
        </ul>
      ) : groups.length === 0 ? (
        <EmptyState icon={<Radio />} title={t("live.emptyTitle")} description={t("live.empty")} />
      ) : (
        <>
          <div role="group" aria-label={t("live.groups")} className="flex flex-wrap gap-2">
            {[{ id: undefined, name: t("live.allGroups") }, ...groups].map((item) => (
              <Button
                key={item.id ?? "all"}
                size="sm"
                variant={item.id === selected ? "primary" : "secondary"}
                aria-pressed={item.id === selected}
                onClick={() => {
                  void navigate({
                    search: item.id === undefined ? {} : { group: item.id },
                    replace: true,
                  });
                }}
              >
                {item.name}
              </Button>
            ))}
          </div>
          <ul
            className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"
            aria-label={t("live.channels")}
          >
            {channels.map((channel) => (
              <ChannelCard
                key={channel.id}
                channel={channel}
                now={now}
                onGuide={() => {
                  setGuideFor(channel);
                }}
              />
            ))}
          </ul>
        </>
      )}
      <GuideDrawer
        channel={guideFor}
        onClose={() => {
          setGuideFor(null);
        }}
      />
    </div>
  );
}
