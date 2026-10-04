import {
  TranscodeProfile,
  apiFetch,
  getMoviesRetrieveQueryKey,
  getSeriesRetrieveQueryKey,
  getTitlesImagesCreateUrl,
  getTitlesImagesListQueryKey,
  getTitlesRenditionsListQueryKey,
  getTitlesTracksCreateUrl,
  useMetadataSearch,
  useTitlesImagesCreate,
  useTitlesImagesDelete,
  useTitlesImagesList,
  useTitlesImagesPrimary,
  useTitlesRematch,
  useTitlesRenditionsDelete,
  useTitlesRenditionsList,
  useTitlesReprocess,
  useTitlesTracksDelete,
  useTitlesTracksUpdate,
  type Image,
  type ImageAddKindEnum,
  type MediaSubtitleTrack,
  type Rendition,
  type TitleFile,
} from "@smart-iptv/api";
import {
  BackdropImage,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Checkbox,
  ConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  Label,
  PosterImage,
  ProgressBar,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  Skeleton,
  StatusBadge,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  cn,
  toast,
  useFormatters,
} from "@smart-iptv/ui";
import { useQueryClient } from "@tanstack/react-query";
import { FileVideo, ImagePlus, Repeat, RotateCcw, Star, Trash2, Upload } from "lucide-react";
import { useDeferredValue, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { QueryError } from "../../components/states";
import { useCan } from "../../lib/auth";
import { notifyError, translatedFieldErrors } from "../../lib/problems";
import { imageSource } from "../catalog/artwork";

/** How often the panel refreshes while jobs run or renditions are pending. */
const BUSY_REFRESH_MS = 5_000;
const OUTPUTS = Object.values(TranscodeProfile);
const IMAGE_KINDS: readonly ImageAddKindEnum[] = ["poster", "backdrop", "logo"];

function useRefreshMedia(titleId: string) {
  const queryClient = useQueryClient();
  return async () => {
    await queryClient.invalidateQueries({ queryKey: getTitlesRenditionsListQueryKey(titleId) });
  };
}

function resolution(file: TitleFile): string {
  return file.width !== null && file.height !== null
    ? `${String(file.width)}×${String(file.height)}`
    : "";
}

function RenditionRow({
  titleId,
  rendition,
  onDelete,
}: {
  titleId: string;
  rendition: Rendition;
  onDelete: (() => void) | null;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  return (
    <TableRow data-rendition={rendition.id} data-title={titleId} className="whitespace-nowrap">
      <TableCell>
        <span className="grid">
          <span className="font-mono text-xs text-foreground" dir="ltr">
            {rendition.name || rendition.kind}
          </span>
          <span className="text-xs text-muted-foreground">
            {t(`titleMedia.kinds.${rendition.kind}`, { defaultValue: rendition.kind })}
          </span>
        </span>
      </TableCell>
      <TableCell>
        <StatusBadge status={rendition.status} />
        {rendition.error ? (
          <span
            className="mt-1 block max-w-60 truncate text-xs text-danger-text"
            title={rendition.error}
          >
            {rendition.error}
          </span>
        ) : null}
      </TableCell>
      <TableCell className="tabular-nums" dir="ltr">
        {rendition.height ? `${String(rendition.height)}p` : ""}
        {rendition.codec ? ` · ${rendition.codec}` : ""}
      </TableCell>
      <TableCell className="text-end tabular-nums">
        {rendition.bitrate ? format.bitrate(rendition.bitrate) : ""}
      </TableCell>
      <TableCell className="text-end tabular-nums">
        {rendition.disk_bytes > 0 ? format.bytes(rendition.disk_bytes) : t("titleMedia.linked")}
      </TableCell>
      <TableCell>
        {rendition.encoder_used ? (
          <Badge className="font-mono" dir="ltr">
            {rendition.encoder_used}
          </Badge>
        ) : null}
      </TableCell>
      <TableCell className="text-end">
        {onDelete ? (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t("titleMedia.deleteRendition", { name: rendition.name || rendition.kind })}
            onClick={onDelete}
          >
            <Trash2 aria-hidden="true" />
          </Button>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

function SubtitleDialog({
  titleId,
  track,
  file,
  open,
  onOpenChange,
}: {
  titleId: string;
  /** Editing this track; null uploads a new one to `file`. */
  track: MediaSubtitleTrack | null;
  file: TitleFile;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const ids = { file: useId(), language: useId(), title: useId() };
  const refresh = useRefreshMedia(titleId);
  const update = useTitlesTracksUpdate();
  const [upload, setUpload] = useState<File | null>(null);
  const [language, setLanguage] = useState(track?.language ?? "ar");
  const [label, setLabel] = useState(track?.title ?? "");
  const [isDefault, setDefault] = useState(track?.default ?? false);
  const [forced, setForced] = useState(track?.forced ?? false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(): Promise<void> {
    setError(null);
    setPending(true);
    try {
      if (track) {
        await update.mutateAsync({
          id: titleId,
          track: track.id,
          data: { language: language.trim(), title: label.trim(), default: isDefault, forced },
        });
      } else if (upload) {
        const body = new FormData();
        body.append("file", upload);
        body.append("language", language.trim());
        body.append("title", label.trim());
        body.append("default", String(isDefault));
        body.append("forced", String(forced));
        body.append("media_file", file.id);
        await apiFetch(getTitlesTracksCreateUrl(titleId), { method: "POST", body });
      }
      await refresh();
      toast.success(track ? t("titleMedia.subtitles.saved") : t("titleMedia.subtitles.uploaded"));
      onOpenChange(false);
    } catch (failure) {
      const messages = translatedFieldErrors(t, failure);
      if (messages[0]) setError(messages[0]);
      else notifyError(t, failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {track ? t("titleMedia.subtitles.editTitle") : t("titleMedia.subtitles.uploadTitle")}
          </DialogTitle>
          <DialogDescription>{t("titleMedia.subtitles.description")}</DialogDescription>
        </DialogHeader>
        <form
          noValidate
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          {track ? null : (
            <div className="grid gap-1.5">
              <Label htmlFor={ids.file}>{t("titleMedia.subtitles.file")}</Label>
              <Input
                id={ids.file}
                type="file"
                accept=".srt,.vtt,.ass,.ssa"
                onChange={(event) => {
                  setUpload(event.target.files?.[0] ?? null);
                }}
              />
            </div>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor={ids.language}>{t("titleMedia.subtitles.language")}</Label>
              <Input
                id={ids.language}
                dir="ltr"
                maxLength={16}
                value={language}
                onChange={(event) => {
                  setLanguage(event.target.value);
                }}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={ids.title}>{t("titleMedia.subtitles.label")}</Label>
              <Input
                id={ids.title}
                dir="auto"
                maxLength={200}
                value={label}
                onChange={(event) => {
                  setLabel(event.target.value);
                }}
              />
            </div>
          </div>
          <div className="flex flex-wrap gap-4">
            <Label className="flex items-center gap-2 font-normal">
              <Checkbox
                checked={isDefault}
                onCheckedChange={(value) => {
                  setDefault(value === true);
                }}
              />
              {t("titleMedia.subtitles.default")}
            </Label>
            <Label className="flex items-center gap-2 font-normal">
              <Checkbox
                checked={forced}
                onCheckedChange={(value) => {
                  setForced(value === true);
                }}
              />
              {t("titleMedia.subtitles.forced")}
            </Label>
          </div>
          {error ? <p className="text-xs font-medium text-danger-text">{error}</p> : null}
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
            <Button
              type="submit"
              pending={pending}
              disabled={(!track && upload === null) || language.trim() === ""}
            >
              {track ? t("common.save") : t("titleMedia.subtitles.upload")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function FileMedia({
  titleId,
  file,
  manage,
}: {
  titleId: string;
  file: TitleFile;
  manage: boolean;
}) {
  const { t } = useTranslation();
  const format = useFormatters();
  const refresh = useRefreshMedia(titleId);
  const removeRendition = useTitlesRenditionsDelete();
  const removeTrack = useTitlesTracksDelete();
  const [deleting, setDeleting] = useState<Rendition | null>(null);
  const [subtitle, setSubtitle] = useState<{ track: MediaSubtitleTrack | null } | null>(null);
  const [deletingTrack, setDeletingTrack] = useState<MediaSubtitleTrack | null>(null);
  return (
    <section
      className="grid gap-3 rounded-input border border-border p-3"
      aria-label={file.relative_path}
    >
      <div className="flex flex-wrap items-center gap-2">
        <FileVideo aria-hidden="true" className="size-4 text-muted-foreground" />
        <bdi dir="ltr" className="min-w-0 break-all font-mono text-xs text-foreground">
          {file.relative_path}
        </bdi>
        {file.is_primary ? <Badge tone="primary">{t("titleMedia.primary")}</Badge> : null}
      </div>
      <div className="flex flex-wrap gap-1.5 text-xs text-muted-foreground">
        <Badge dir="ltr">{file.video_codec || t("titleMedia.unknown")}</Badge>
        {resolution(file) ? <Badge dir="ltr">{resolution(file)}</Badge> : null}
        {file.hdr && file.hdr !== "sdr" ? (
          <Badge tone="warning">{file.hdr.toUpperCase()}</Badge>
        ) : null}
        <span className="tabular-nums">
          {format.bytes(file.size)}
          {file.duration_ms ? ` · ${format.duration(file.duration_ms / 1000)}` : ""}
        </span>
        <span>{t("titleMedia.disk", { size: format.bytes(file.disk_bytes) })}</span>
      </div>
      {file.jobs.length > 0 ? (
        <div className="grid gap-2">
          {file.jobs.map((job) => (
            <ProgressBar
              key={job.id}
              value={job.status === "running" ? job.progress : null}
              etaSeconds={job.eta_s}
              label={t("titleMedia.job", {
                profile: t(`titleMedia.outputs.${job.profile}`),
                backend: t(`transcode.backends.${job.backend}`, { defaultValue: job.backend }),
                status: t(`ui:status.${job.status}`),
              })}
            />
          ))}
        </div>
      ) : null}
      <div className="overflow-x-auto">
        <Table aria-label={t("titleMedia.renditions")}>
          <TableHeader>
            <TableRow>
              <TableHead>{t("titleMedia.columns.output")}</TableHead>
              <TableHead>{t("titleMedia.columns.status")}</TableHead>
              <TableHead>{t("titleMedia.columns.video")}</TableHead>
              <TableHead className="text-end">{t("titleMedia.columns.bitrate")}</TableHead>
              <TableHead className="text-end">{t("titleMedia.columns.size")}</TableHead>
              <TableHead>{t("titleMedia.columns.encoder")}</TableHead>
              <TableHead>
                <span className="sr-only">{t("titleMedia.columns.actions")}</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {file.renditions.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="text-muted-foreground">
                  {t("titleMedia.noRenditions")}
                </TableCell>
              </TableRow>
            ) : (
              file.renditions.map((rendition) => (
                <RenditionRow
                  key={rendition.id}
                  titleId={titleId}
                  rendition={rendition}
                  onDelete={
                    manage && rendition.kind !== "source"
                      ? () => {
                          setDeleting(rendition);
                        }
                      : null
                  }
                />
              ))
            )}
          </TableBody>
        </Table>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <div className="grid content-start gap-1.5">
          <h3 className="text-xs font-medium text-muted-foreground">{t("titleMedia.audio")}</h3>
          {file.audio.length === 0 ? (
            <p className="text-ui text-muted-foreground">{t("titleMedia.none")}</p>
          ) : (
            <ul className="grid gap-1">
              {file.audio.map((track) => (
                <li key={track.id} className="flex flex-wrap items-center gap-1.5 text-ui">
                  <Badge dir="ltr">{track.language || "und"}</Badge>
                  <span dir="ltr" className="text-xs text-muted-foreground">
                    {t("titleMedia.audioFormat", { codec: track.codec, channels: track.channels })}
                  </span>
                  {track.title ? <bdi className="truncate">{track.title}</bdi> : null}
                  {track.default ? (
                    <Badge tone="primary">{t("titleMedia.subtitles.default")}</Badge>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="grid content-start gap-1.5">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-xs font-medium text-muted-foreground">
              {t("titleMedia.subtitles.title")}
            </h3>
            {manage ? (
              <Button
                variant="ghost"
                size="xs"
                onClick={() => {
                  setSubtitle({ track: null });
                }}
              >
                <Upload aria-hidden="true" />
                {t("titleMedia.subtitles.upload")}
              </Button>
            ) : null}
          </div>
          {file.subtitles.length === 0 ? (
            <p className="text-ui text-muted-foreground">{t("titleMedia.none")}</p>
          ) : (
            <ul className="grid gap-1">
              {file.subtitles.map((track) => (
                <li key={track.id} className="flex flex-wrap items-center gap-1.5 text-ui">
                  <Badge dir="ltr">{track.language || "und"}</Badge>
                  <span dir="ltr" className="text-xs text-muted-foreground">
                    {track.format}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {t(`titleMedia.origins.${track.origin}`)}
                  </span>
                  {track.title ? <bdi className="truncate">{track.title}</bdi> : null}
                  {track.default ? (
                    <Badge tone="primary">{t("titleMedia.subtitles.default")}</Badge>
                  ) : null}
                  {track.forced ? <Badge>{t("titleMedia.subtitles.forced")}</Badge> : null}
                  {track.status !== "ready" ? (
                    <StatusBadge
                      status={track.status}
                      label={t(`titleMedia.subtitleStatus.${track.status}`)}
                    />
                  ) : null}
                  {manage ? (
                    <span className="ms-auto flex gap-1">
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={() => {
                          setSubtitle({ track });
                        }}
                      >
                        {t("titleMedia.subtitles.edit")}
                      </Button>
                      {track.origin === "upload" ? (
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label={t("titleMedia.subtitles.delete", {
                            language: track.language,
                          })}
                          onClick={() => {
                            setDeletingTrack(track);
                          }}
                        >
                          <Trash2 aria-hidden="true" />
                        </Button>
                      ) : null}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      {subtitle ? (
        <SubtitleDialog
          titleId={titleId}
          track={subtitle.track}
          file={file}
          open
          onOpenChange={(open) => {
            if (!open) setSubtitle(null);
          }}
        />
      ) : null}
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        tone="danger"
        title={t("titleMedia.deleteTitle")}
        description={t("titleMedia.deleteDescription", { name: deleting?.name ?? "" })}
        confirmLabel={t("titleMedia.deleteConfirm")}
        onConfirm={async () => {
          if (deleting === null) return;
          try {
            await removeRendition.mutateAsync({ id: titleId, rendition: deleting.id });
            await refresh();
            toast.success(t("titleMedia.deleted"));
          } catch (error) {
            notifyError(t, error);
            throw error;
          }
        }}
      />
      <ConfirmDialog
        open={deletingTrack !== null}
        onOpenChange={(open) => {
          if (!open) setDeletingTrack(null);
        }}
        tone="danger"
        title={t("titleMedia.subtitles.deleteTitle")}
        description={t("titleMedia.subtitles.deleteDescription")}
        confirmLabel={t("titleMedia.subtitles.deleteConfirm")}
        onConfirm={async () => {
          if (deletingTrack === null) return;
          try {
            await removeTrack.mutateAsync({ id: titleId, track: deletingTrack.id });
            await refresh();
            toast.success(t("titleMedia.subtitles.deleted"));
          } catch (error) {
            notifyError(t, error);
            throw error;
          }
        }}
      />
    </section>
  );
}

function ReprocessDialog({
  titleId,
  open,
  onOpenChange,
}: {
  titleId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const refresh = useRefreshMedia(titleId);
  const reprocess = useTitlesReprocess();
  const [outputs, setOutputs] = useState<string[]>([]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("titleMedia.reprocess.title")}</DialogTitle>
          <DialogDescription>{t("titleMedia.reprocess.description")}</DialogDescription>
        </DialogHeader>
        <fieldset className="grid gap-2">
          <legend className="mb-2 text-ui font-medium text-foreground">
            {t("titleMedia.reprocess.outputs")}
          </legend>
          {OUTPUTS.map((output) => (
            <Label key={output} className="flex items-center gap-2 font-normal">
              <Checkbox
                checked={outputs.includes(output)}
                onCheckedChange={(value) => {
                  setOutputs((current) =>
                    value === true
                      ? [...current, output]
                      : current.filter((item) => item !== output),
                  );
                }}
              />
              {t(`titleMedia.outputs.${output}`)}
            </Label>
          ))}
          <p className="text-xs text-muted-foreground">{t("titleMedia.reprocess.allHelp")}</p>
        </fieldset>
        <DialogFooter>
          <Button
            variant="secondary"
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("common.cancel")}
          </Button>
          <Button
            pending={reprocess.isPending}
            onClick={() => {
              reprocess.mutate(
                {
                  id: titleId,
                  data: outputs.length > 0 ? { outputs: outputs as TranscodeProfile[] } : {},
                },
                {
                  onSuccess: (result) => {
                    toast.success(t("titleMedia.reprocess.done", { count: result.files.length }));
                    void refresh();
                    onOpenChange(false);
                  },
                  onError: (error) => {
                    notifyError(t, error);
                  },
                },
              );
            }}
          >
            {t("titleMedia.reprocess.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function useTitleMedia(titleId: string) {
  return useTitlesRenditionsList(titleId, {
    query: {
      refetchInterval: (state) => {
        const files = state.state.data?.files ?? [];
        const busy = files.some(
          (file) =>
            file.jobs.length > 0 ||
            file.renditions.some((item) => item.status === "pending" || item.status === "running"),
        );
        return busy ? BUSY_REFRESH_MS : false;
      },
    },
  });
}

function MediaFiles({ titleId }: { titleId: string }) {
  const { t } = useTranslation();
  const can = useCan();
  const query = useTitleMedia(titleId);
  if (query.isPending) {
    return (
      <div role="status">
        <span className="sr-only">{t("layout.loading")}</span>
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (query.isError) {
    return (
      <QueryError
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  if (query.data.files.length === 0) {
    return (
      <EmptyState
        icon={<FileVideo />}
        title={t("titles.files.none")}
        description={t("titles.files.noneHelp")}
      />
    );
  }
  return (
    <div className="grid gap-4">
      {query.data.files.map((file) => (
        <FileMedia key={file.id} titleId={titleId} file={file} manage={can("library.manage")} />
      ))}
    </div>
  );
}

/** "Reprocess" button with its dialog; `{id}` may be a movie, a series (every episode) or an episode. */
export function ReprocessButton({ titleId }: { titleId: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="secondary"
        onClick={() => {
          setOpen(true);
        }}
      >
        <RotateCcw aria-hidden="true" />
        {t("titleMedia.reprocess.action")}
      </Button>
      {open ? <ReprocessDialog titleId={titleId} open={open} onOpenChange={setOpen} /> : null}
    </>
  );
}

/**
 * Files & renditions (SPEC §8.3.6): each file's probe summary, its renditions
 * with status, size and encoder, running jobs, audio and subtitle tracks, and
 * actions (reprocess, delete a rendition, upload or edit subtitles).
 */
export function MediaPanel({ titleId }: { titleId: string }) {
  const { t } = useTranslation();
  const can = useCan();
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <CardTitle>{t("titleMedia.title")}</CardTitle>
          <CardDescription>{t("titleMedia.description")}</CardDescription>
        </div>
        {can("library.manage") ? <ReprocessButton titleId={titleId} /> : null}
      </CardHeader>
      <CardContent>
        <MediaFiles titleId={titleId} />
      </CardContent>
    </Card>
  );
}

/** One episode's files and renditions, opened from the seasons table. */
export function EpisodeMediaSheet({
  episode,
  onOpenChange,
}: {
  /** The episode shown; null closes the sheet. */
  episode: { id: string; code: string; title: string } | null;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const can = useCan();
  return (
    <Sheet open={episode !== null} onOpenChange={onOpenChange}>
      <SheetContent className="max-w-3xl">
        <SheetHeader>
          <SheetTitle>
            <bdi dir="ltr">{episode?.code}</bdi>{" "}
            {episode?.title ? <bdi>{episode.title}</bdi> : null}
          </SheetTitle>
          <SheetDescription>{t("titleMedia.description")}</SheetDescription>
        </SheetHeader>
        <SheetBody className="flex flex-col gap-4 [&>*]:shrink-0">
          {episode ? (
            <>
              {can("library.manage") ? (
                <div>
                  <ReprocessButton titleId={episode.id} />
                </div>
              ) : null}
              <MediaFiles titleId={episode.id} />
            </>
          ) : null}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}

function ImageTile({
  image,
  onPrimary,
  onDelete,
}: {
  image: Image;
  onPrimary: (() => void) | null;
  onDelete: (() => void) | null;
}) {
  const { t } = useTranslation();
  const wide = image.kind !== "poster" && image.kind !== "profile";
  const Artwork = wide ? BackdropImage : PosterImage;
  return (
    <li className={cn("grid content-start gap-1.5", wide ? "col-span-2" : "")}>
      <Artwork
        src={imageSource(image)}
        blurhash={image.blurhash}
        sizes={wide ? "320px" : "160px"}
        alt={t(`titleMedia.images.kinds.${image.kind}`)}
        className="w-full rounded-input"
      />
      <span className="flex flex-wrap items-center gap-1">
        {image.is_primary ? (
          <Badge tone="primary">
            <Star aria-hidden="true" />
            {t("titleMedia.images.primary")}
          </Badge>
        ) : onPrimary ? (
          <Button variant="ghost" size="xs" onClick={onPrimary}>
            {t("titleMedia.images.makePrimary")}
          </Button>
        ) : null}
        {image.language ? <Badge dir="ltr">{image.language}</Badge> : null}
        {onDelete ? (
          <Button
            variant="ghost"
            size="icon-xs"
            className="ms-auto"
            aria-label={t("titleMedia.images.delete")}
            onClick={onDelete}
          >
            <Trash2 aria-hidden="true" />
          </Button>
        ) : null}
      </span>
    </li>
  );
}

/** Artwork (SPEC §8.3.6 image picker): stored images, TMDB alternatives, or an upload. */
export function ImagesPanel({ titleId, kind }: { titleId: string; kind: "movie" | "series" }) {
  const { t } = useTranslation();
  const can = useCan();
  const manage = can("library.manage");
  const queryClient = useQueryClient();
  const ids = { kind: useId(), file: useId() };
  const query = useTitlesImagesList(titleId);
  const add = useTitlesImagesCreate();
  const primary = useTitlesImagesPrimary();
  const remove = useTitlesImagesDelete();
  const [imageKind, setImageKind] = useState<ImageAddKindEnum>("poster");
  const [uploading, setUploading] = useState(false);
  const [deleting, setDeleting] = useState<Image | null>(null);

  async function refresh(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: getTitlesImagesListQueryKey(titleId) }),
      queryClient.invalidateQueries({
        queryKey:
          kind === "movie"
            ? getMoviesRetrieveQueryKey(titleId)
            : getSeriesRetrieveQueryKey(titleId),
      }),
    ]);
  }

  async function upload(file: File): Promise<void> {
    setUploading(true);
    try {
      const body = new FormData();
      body.append("kind", imageKind);
      body.append("file", file);
      body.append("primary", "true");
      await apiFetch(getTitlesImagesCreateUrl(titleId), { method: "POST", body });
      toast.success(t("titleMedia.images.queued"));
      setTimeout(() => {
        void refresh();
      }, 3000);
    } catch (error) {
      notifyError(t, error);
    } finally {
      setUploading(false);
    }
  }

  const alternatives = (query.data?.alternatives ?? []).filter((item) => item.kind === imageKind);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("titleMedia.images.title")}</CardTitle>
        <CardDescription>{t("titleMedia.images.description")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {query.isPending ? (
          <Skeleton className="h-40 w-full" />
        ) : query.isError ? (
          <QueryError error={query.error} />
        ) : (
          <>
            <ul className="grid grid-cols-4 gap-3 sm:grid-cols-6 xl:grid-cols-8">
              {query.data.images.map((image) => (
                <ImageTile
                  key={image.id}
                  image={image}
                  onPrimary={
                    manage
                      ? () => {
                          primary.mutate(
                            { id: titleId, image: image.id },
                            {
                              onSuccess: () => {
                                toast.success(t("titleMedia.images.primarySet"));
                                void refresh();
                              },
                              onError: (error) => {
                                notifyError(t, error);
                              },
                            },
                          );
                        }
                      : null
                  }
                  onDelete={
                    manage
                      ? () => {
                          setDeleting(image);
                        }
                      : null
                  }
                />
              ))}
            </ul>
            {manage ? (
              <div className="grid gap-3 border-t border-border pt-4">
                <div className="flex flex-wrap items-end gap-3">
                  <div className="grid gap-1.5">
                    <Label htmlFor={ids.kind}>{t("titleMedia.images.kind")}</Label>
                    <Select
                      value={imageKind}
                      onValueChange={(value) => {
                        setImageKind(value as ImageAddKindEnum);
                      }}
                    >
                      <SelectTrigger id={ids.kind} className="w-40">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {IMAGE_KINDS.map((item) => (
                          <SelectItem key={item} value={item}>
                            {t(`titleMedia.images.kinds.${item}`)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="grid gap-1.5">
                    <Label htmlFor={ids.file}>{t("titleMedia.images.upload")}</Label>
                    <Input
                      id={ids.file}
                      type="file"
                      accept="image/jpeg,image/png,image/webp"
                      disabled={uploading}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) void upload(file);
                        event.target.value = "";
                      }}
                    />
                  </div>
                  {uploading ? (
                    <ImagePlus aria-hidden="true" className="size-4 animate-pulse" />
                  ) : null}
                </div>
                <h3 className="text-xs font-medium text-muted-foreground">
                  {t("titleMedia.images.alternatives")}
                </h3>
                {query.data.alternatives_error ? (
                  <p className="text-ui text-muted-foreground">
                    {t("titleMedia.images.unavailable")}
                  </p>
                ) : alternatives.length === 0 ? (
                  <p className="text-ui text-muted-foreground">
                    {t("titleMedia.images.noAlternatives")}
                  </p>
                ) : (
                  <ul className="grid grid-cols-4 gap-3 sm:grid-cols-6 xl:grid-cols-8">
                    {alternatives.slice(0, 12).map((item) => (
                      <li key={item.path}>
                        <button
                          type="button"
                          className="grid w-full gap-1 rounded-input text-start outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          disabled={add.isPending}
                          onClick={() => {
                            add.mutate(
                              {
                                id: titleId,
                                data: { kind: imageKind, tmdb_path: item.path, primary: true },
                              },
                              {
                                onSuccess: () => {
                                  toast.success(t("titleMedia.images.queued"));
                                  setTimeout(() => {
                                    void refresh();
                                  }, 3000);
                                },
                                onError: (error) => {
                                  notifyError(t, error);
                                },
                              },
                            );
                          }}
                        >
                          <img
                            src={item.preview_url}
                            alt={t("titleMedia.images.useAlternative", {
                              size: `${String(item.width)}×${String(item.height)}`,
                            })}
                            loading="lazy"
                            className={cn(
                              "w-full rounded-input border border-border object-cover",
                              imageKind === "backdrop" ? "aspect-video" : "aspect-[2/3]",
                            )}
                          />
                          {item.language ? (
                            <span className="text-xs text-muted-foreground" dir="ltr">
                              {item.language}
                            </span>
                          ) : null}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ) : null}
          </>
        )}
      </CardContent>
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        tone="danger"
        title={t("titleMedia.images.deleteTitle")}
        description={t("titleMedia.images.deleteDescription")}
        confirmLabel={t("titleMedia.images.delete")}
        onConfirm={async () => {
          if (deleting === null) return;
          try {
            await remove.mutateAsync({ id: titleId, image: deleting.id });
            await refresh();
            toast.success(t("titleMedia.images.deleted"));
          } catch (error) {
            notifyError(t, error);
            throw error;
          }
        }}
      />
    </Card>
  );
}

/** Re-match (SPEC §8.3.6): link the files to another TMDB title, or match them again. */
export function RematchDialog({
  titleId,
  kind,
  initialQuery,
  open,
  onOpenChange,
}: {
  titleId: string;
  kind: "movie" | "series";
  initialQuery: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const queryClient = useQueryClient();
  const rematch = useTitlesRematch();
  const [text, setText] = useState(initialQuery);
  const search = useDeferredValue(text.trim());
  const providerKind = kind === "movie" ? "movie" : "tv";
  const results = useMetadataSearch(
    { kind: providerKind, query: search },
    { query: { enabled: open && search.length >= 2 } },
  );

  function run(tmdbId: number | null): void {
    rematch.mutate(
      { id: titleId, data: tmdbId === null ? {} : { tmdb_id: tmdbId, kind: providerKind } },
      {
        onSuccess: (result) => {
          toast.success(
            result.automatic
              ? t("titleMedia.rematch.automaticDone", { count: result.files })
              : t("titleMedia.rematch.done", { count: result.files }),
          );
          void queryClient.invalidateQueries();
          onOpenChange(false);
        },
        onError: (error) => {
          notifyError(t, error);
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("titleMedia.rematch.title")}</DialogTitle>
          <DialogDescription>{t("titleMedia.rematch.description")}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-1.5">
          <Label htmlFor={id}>{t("titleMedia.rematch.search")}</Label>
          <Input
            id={id}
            type="search"
            dir="auto"
            value={text}
            onChange={(event) => {
              setText(event.target.value);
            }}
          />
        </div>
        <div className="max-h-80 overflow-y-auto">
          {results.isFetching ? (
            <Skeleton className="h-24 w-full" />
          ) : results.isError ? (
            <QueryError error={results.error} />
          ) : (
            <ul className="grid gap-2">
              {(results.data ?? []).map((candidate) => (
                <li
                  key={candidate.id}
                  className="flex items-center gap-3 rounded-input border border-border p-2"
                >
                  <span className="grid min-w-0 flex-1">
                    <bdi className="truncate font-medium text-foreground">{candidate.title}</bdi>
                    <span className="text-xs text-muted-foreground" dir="ltr">
                      {candidate.year === null
                        ? t("titleMedia.rematch.tmdbId", { id: candidate.id })
                        : t("titleMedia.rematch.candidate", {
                            year: candidate.year,
                            id: candidate.id,
                          })}
                    </span>
                  </span>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={rematch.isPending}
                    onClick={() => {
                      run(candidate.id);
                    }}
                  >
                    <Repeat aria-hidden="true" />
                    {t("titleMedia.rematch.use")}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <DialogFooter>
          <Button
            variant="secondary"
            pending={rematch.isPending}
            onClick={() => {
              run(null);
            }}
          >
            {t("titleMedia.rematch.automatic")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
