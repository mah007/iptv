import {
  isApiError,
  useEpisodesRetrieve,
  useMoviesRetrieve,
  usePlaybackStart,
  useSeriesRetrieve,
  type PlaybackGrant,
  type PreferEnum,
  type TitleType,
} from "@smart-iptv/api-portal";
import { Button, formatDuration } from "@smart-iptv/ui";
import { getRouteApi, Link, useCanGoBack, useNavigate, useRouter } from "@tanstack/react-router";
import { ArrowLeft, CirclePlay, RotateCcw, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useEpisodeLabel } from "../components/watch-card";
import { playbackProblem, type PlaybackAction } from "../features/player/playback-errors";
import { VideoPlayer, type PlayerFailure } from "../features/player/video-player";
import { titlePath, watchPath } from "../lib/links";
import { usePageTitle } from "../lib/page-title";

const movieApi = getRouteApi("/app/watch/movie/$titleId");
const seriesApi = getRouteApi("/app/watch/series/$titleId");

/** Resume where the viewer stopped unless they asked to start over or there is nothing to resume. */
export function initialStart(grant: PlaybackGrant, restart: boolean): number | "ask" {
  if (restart || grant.resume_ms <= 0) return 0;
  return "ask";
}

function PlayerFrame({ children, onBack }: { children: ReactNode; onBack: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black px-4 text-white">
      <Button
        variant="ghost"
        size="icon"
        className="absolute start-3 top-3 text-white hover:bg-white/15 hover:text-white sm:start-6"
        aria-label={t("player.back")}
        onClick={onBack}
      >
        <ArrowLeft aria-hidden="true" className="rtl:-scale-x-100" />
      </Button>
      <main className="w-full max-w-md">{children}</main>
    </div>
  );
}

function ProblemPanel({
  error,
  onRetry,
  onBack,
}: {
  error: unknown;
  onRetry: () => void;
  onBack: () => void;
}) {
  const { t } = useTranslation();
  const problem = playbackProblem(error);
  const link = (action: PlaybackAction) => {
    switch (action) {
      case "renew":
        return (
          <Button key={action} asChild>
            <Link to="/plans">{t("player.actions.renew")}</Link>
          </Button>
        );
      case "plans":
        return (
          <Button key={action} asChild>
            <Link to="/plans">{t("player.actions.plans")}</Link>
          </Button>
        );
      case "devices":
        return (
          <Button key={action} asChild>
            <Link to="/account/devices">{t("player.actions.devices")}</Link>
          </Button>
        );
      case "account":
        return (
          <Button key={action} asChild variant="secondary">
            <Link to="/account/subscription">{t("player.actions.account")}</Link>
          </Button>
        );
      case "retry":
        return (
          <Button key={action} variant="secondary" onClick={onRetry}>
            <RotateCcw aria-hidden="true" />
            {t("player.actions.retry")}
          </Button>
        );
    }
  };
  return (
    <PlayerFrame onBack={onBack}>
      <div role="alert" className="grid justify-items-center gap-4 text-center">
        <TriangleAlert aria-hidden="true" className="size-10 text-warning" />
        <h1 className="text-xl font-semibold">{t(`player.errors.${problem.code}.title`)}</h1>
        <p className="text-sm text-white/80">{t(`player.errors.${problem.code}.description`)}</p>
        <div className="flex flex-wrap justify-center gap-2">
          {problem.actions.map(link)}
          <Button
            variant="ghost"
            className="text-white hover:bg-white/15 hover:text-white"
            onClick={onBack}
          >
            {t("player.actions.back")}
          </Button>
        </div>
      </div>
    </PlayerFrame>
  );
}

function ResumePrompt({
  resumeMs,
  onResume,
  onRestart,
  onBack,
}: {
  resumeMs: number;
  onResume: () => void;
  onRestart: () => void;
  onBack: () => void;
}) {
  const { t, i18n } = useTranslation();
  return (
    <PlayerFrame onBack={onBack}>
      <div className="grid justify-items-center gap-4 text-center">
        <h1 className="text-xl font-semibold">{t("player.resumeTitle")}</h1>
        <div className="flex flex-wrap justify-center gap-2">
          <Button size="lg" autoFocus onClick={onResume}>
            <CirclePlay aria-hidden="true" />
            {t("player.resumeFrom", {
              time: formatDuration(resumeMs / 1000, i18n.language, "clock"),
            })}
          </Button>
          <Button size="lg" variant="secondary" onClick={onRestart}>
            <RotateCcw aria-hidden="true" />
            {t("player.startOver")}
          </Button>
        </div>
      </div>
    </PlayerFrame>
  );
}

function Loading({ onBack }: { onBack: () => void }) {
  const { t } = useTranslation();
  return (
    <PlayerFrame onBack={onBack}>
      <p role="status" className="text-center text-sm text-white/80">
        {t("player.starting")}
      </p>
    </PlayerFrame>
  );
}

/**
 * Starts a stream (POST playback/start, HLS preferred), asks whether to
 * resume, plays it, and recovers: a missing or unplayable HLS ladder falls
 * back to the MP4; a stopped or expired stream asks for a new grant at the
 * same position.
 */
function Watch({
  kind,
  titleId,
  episodeId,
  restart,
  heading,
  subheading,
  nextEpisodeId,
  nextLabel,
}: {
  kind: TitleType;
  titleId: string;
  episodeId?: string | undefined;
  restart: boolean;
  heading: string;
  subheading?: string | undefined;
  nextEpisodeId?: string | null | undefined;
  nextLabel?: string | undefined;
}) {
  const navigate = useNavigate();
  const router = useRouter();
  const canGoBack = useCanGoBack();
  const start = usePlaybackStart();
  const [grant, setGrant] = useState<PlaybackGrant | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [startAt, setStartAt] = useState<number | "ask" | null>(null);
  const mutate = start.mutate;

  const begin = useCallback(
    (prefer: PreferEnum, at?: number) => {
      mutate(
        {
          data: {
            title_type: kind,
            title_id: titleId,
            ...(episodeId ? { episode_id: episodeId } : {}),
            prefer,
          },
        },
        {
          onSuccess: (granted) => {
            setGrant(granted);
            setStartAt(at ?? initialStart(granted, restart));
          },
          onError: (failure) => {
            setGrant(null);
            setError(failure);
          },
        },
      );
    },
    [mutate, kind, titleId, episodeId, restart],
  );

  // The page is keyed by what plays, so this runs once per title or episode.
  useEffect(() => {
    begin("hls");
  }, [begin]);

  const back = useCallback(() => {
    if (canGoBack) router.history.back();
    else void navigate({ ...titlePath(kind, titleId), replace: true });
  }, [canGoBack, kind, navigate, router.history, titleId]);

  const onFailure = useCallback(
    (failure: PlayerFailure, positionS: number) => {
      if (failure === "HLS_UNAVAILABLE") {
        begin("mp4", positionS);
        return;
      }
      setGrant(null);
      setError(failure);
      setStartAt(positionS);
    },
    [begin],
  );

  if (error !== null) {
    const resumeAt = typeof startAt === "number" ? startAt : undefined;
    return (
      <ProblemPanel
        error={error}
        onBack={back}
        onRetry={() => {
          setError(null);
          begin("hls", resumeAt);
        }}
      />
    );
  }
  if (grant === null || startAt === null) return <Loading onBack={back} />;
  if (startAt === "ask") {
    return (
      <ResumePrompt
        resumeMs={grant.resume_ms}
        onBack={back}
        onResume={() => {
          setStartAt(grant.resume_ms / 1000);
        }}
        onRestart={() => {
          setStartAt(0);
        }}
      />
    );
  }
  const next =
    kind === "series" && nextEpisodeId
      ? {
          label: nextLabel ?? "",
          play: () => {
            void navigate({
              ...watchPath("series", titleId, { episode: nextEpisodeId }),
              replace: true,
            });
          },
        }
      : null;
  return (
    <VideoPlayer
      key={grant.url}
      grant={grant}
      heading={heading}
      subheading={subheading}
      startAt={startAt}
      onBack={back}
      onFailure={onFailure}
      next={next}
    />
  );
}

export function WatchMoviePage() {
  const { t } = useTranslation();
  const { titleId } = movieApi.useParams();
  const { restart } = movieApi.useSearch();
  const movie = useMoviesRetrieve(titleId);
  const heading = movie.data?.title ?? t("player.title");
  usePageTitle(heading);
  return (
    <Watch
      key={titleId}
      kind="movie"
      titleId={titleId}
      restart={restart === true}
      heading={heading}
    />
  );
}

export function WatchSeriesPage() {
  const { t } = useTranslation();
  const episodeLabel = useEpisodeLabel();
  const { titleId } = seriesApi.useParams();
  const { episode: episodeParam, restart } = seriesApi.useSearch();
  const series = useSeriesRetrieve(titleId);
  // Without ?episode the API plays the episode in progress or the next one: the series says which.
  const episodeId = episodeParam ?? series.data?.next_episode?.id;
  const episode = useEpisodesRetrieve(episodeId ?? "", {
    query: { enabled: episodeId !== undefined },
  });
  const next = useEpisodesRetrieve(episode.data?.next_id ?? "", {
    query: { enabled: Boolean(episode.data?.next_id) },
  });
  const heading = series.data?.title ?? t("player.title");
  const subheading = episode.data
    ? `${episodeLabel(episode.data.season_number, episode.data.number)} · ${episode.data.title}`
    : undefined;
  usePageTitle(subheading ? `${heading} · ${subheading}` : heading);
  if (isApiError(series.error) && series.error.code === "NOT_FOUND") {
    return <Watch kind="series" titleId={titleId} restart={false} heading={heading} />;
  }
  if (episodeParam === undefined && series.isPending) return null;
  return (
    <Watch
      key={`${titleId}:${episodeId ?? "next"}`}
      kind="series"
      titleId={titleId}
      episodeId={episodeId}
      restart={restart === true}
      heading={heading}
      subheading={subheading}
      nextEpisodeId={episode.data?.next_id}
      nextLabel={
        next.data
          ? `${episodeLabel(next.data.season_number, next.data.number)} · ${next.data.title}`
          : undefined
      }
    />
  );
}
