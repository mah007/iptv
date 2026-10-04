"""Admin services of a title's media (SPEC §8.3 Title detail, §10 `admin/titles/{id}/...`).

A title id is a movie's, a series' or an episode's (UUIDv7 ids never collide). These
services back the title page's panels:

- **renditions:** each file's outputs with status, size and disk use; delete one;
- **reprocess:** make outputs again (queued on the worker, which can read the media);
- **tracks:** audio and subtitle tracks; edit language, title, default and forced flags;
  upload a sidecar subtitle; delete an upload;
- **images:** stored artwork plus TMDB's alternatives; pick one, upload one, make one
  primary, delete one;
- **rematch:** link the title's files to another TMDB title, or match them again.

The web process never touches the media volume: everything that reads or writes it
runs as a Celery task on the worker (`apps.media.tasks`).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal
from uuid import UUID

import structlog
from django.db import transaction
from django.db.models import Prefetch, Q, QuerySet

from apps.accounts.models import User
from apps.audit import services as audit
from apps.catalog.models import (
    Episode,
    FileState,
    ImageKind,
    MediaFile,
    MediaImage,
    Movie,
    Series,
)
from apps.catalog.services import refresh_status_of_files
from apps.catalog.signals import notify_catalog_changed
from apps.core.errors import ErrorCode, ProblemError
from apps.media import layout, subtitles
from apps.media.models import (
    ACTIVE_JOB_STATUSES,
    SUBTITLE_MAX_BYTES,
    AudioTrack,
    Rendition,
    RenditionKind,
    SubtitleStatus,
    SubtitleTrack,
    TranscodeJob,
    TranscodeProfile,
)
from apps.media.probe import normalize_language

logger = structlog.get_logger(__name__)

type TitleKind = Literal["movie", "series", "episode"]
#: Largest artwork upload (the image pipeline refuses bigger pictures anyway).
IMAGE_MAX_BYTES: Final = 15 * 1024 * 1024
IMAGE_KINDS: Final = (ImageKind.POSTER, ImageKind.BACKDROP, ImageKind.LOGO, ImageKind.STILL)
ALTERNATIVES_PER_KIND: Final = 12


@dataclass(frozen=True, slots=True)
class Title:
    kind: TitleKind
    id: UUID
    name: str
    tmdb_id: int | None
    obj: Movie | Series | Episode


def _not_found() -> ProblemError:
    return ProblemError(ErrorCode.NOT_FOUND, "No movie, series or episode has this id.", status=404)


def _conflict(detail: str) -> ProblemError:
    return ProblemError(ErrorCode.CONFLICT, detail, status=409)


def _invalid(field: str, message: str) -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_ERROR, message, status=400, field_errors={field: [message]}
    )


def resolve_title(pk: UUID) -> Title:
    movie = Movie.objects.filter(pk=pk).first()
    if movie is not None:
        return Title("movie", movie.pk, str(movie.title), movie.tmdb_id, movie)
    series = Series.objects.filter(pk=pk).first()
    if series is not None:
        return Title("series", series.pk, str(series.title), series.tmdb_id, series)
    episode = Episode.objects.select_related("season__series").filter(pk=pk).first()
    if episode is not None:
        series = episode.season.series
        name = f"{series.title} S{episode.season.number:02d}E{episode.number:02d}"
        return Title("episode", episode.pk, name, series.tmdb_id, episode)
    raise _not_found()


def title_files(title: Title) -> QuerySet[MediaFile]:
    """The title's files on disk (a series: every episode's), primary first."""
    files = MediaFile.objects.filter(removed_at__isnull=True).select_related("library")
    if title.kind == "movie":
        files = files.filter(movie_id=title.id)
    elif title.kind == "series":
        files = files.filter(episodes__season__series_id=title.id)
    else:
        files = files.filter(episodes__id=title.id)
    return files.distinct().order_by("-is_primary", "storage_key")


def files_with_media(title: Title) -> list[MediaFile]:
    """`title_files` with renditions, tracks and active jobs prefetched (5 queries)."""
    return list(
        title_files(title).prefetch_related(
            Prefetch("renditions", queryset=Rendition.objects.order_by("kind", "name")),
            Prefetch("audio_tracks", queryset=AudioTrack.objects.order_by("stream_index")),
            Prefetch(
                "subtitle_tracks",
                queryset=SubtitleTrack.objects.defer("upload").order_by(
                    "external", "stream_index", "created_at"
                ),
            ),
            Prefetch(
                "transcode_jobs",
                queryset=TranscodeJob.objects.filter(status__in=ACTIVE_JOB_STATUSES).order_by(
                    "created_at"
                ),
                to_attr="active_jobs",
            ),
        )
    )


def _file_of(title: Title, file_id: UUID | None) -> MediaFile:
    files = list(title_files(title))
    if file_id is not None:
        match = next((f for f in files if f.pk == file_id), None)
        if match is None:
            raise _invalid("media_file", "This file does not belong to the title.")
        return match
    if not files:
        raise _conflict("The title has no file on disk.")
    if len(files) > 1:
        raise _invalid("media_file", "The title has several files: choose one.")
    return files[0]


def _queue(task_name: str, *args: Any) -> None:
    from apps.media import tasks  # noqa: PLC0415 (tasks import the services)

    task = getattr(tasks, task_name)
    transaction.on_commit(lambda: task.delay(*args))


# --- Renditions -----------------------------------------------------------------------------


def disk_bytes(rendition: Rendition) -> int:
    """Bytes the rendition occupies on the media volume (a link to the source: none)."""
    return 0 if (rendition.details or {}).get("linked") else rendition.size


#: What deleting a rendition removes with it (rows; the files go with the next cleanup).
_DELETE_WITH: Final[dict[str, Q]] = {
    RenditionKind.HLS_MASTER: Q(kind__in=(RenditionKind.HLS_MASTER, RenditionKind.HLS_VARIANT))
    & ~Q(name=layout.UHD_RUNG),
    RenditionKind.UHD: Q(kind=RenditionKind.UHD)
    | Q(kind=RenditionKind.HLS_VARIANT, name=layout.UHD_RUNG)
    | Q(kind=RenditionKind.HLS_MASTER, name=layout.presentation(layout.UHD_CEILING)),
}


def delete_rendition(
    title: Title, rendition_id: UUID, *, actor: User | None, ip: str | None
) -> int:
    """Delete an output (with the rows that depend on it: the ladder's rungs and capped
    presentations, the UHD rung). Players stop getting it at once; its files are
    deleted by the next cleanup. The direct-play source cannot be deleted. Returns the
    number of rows deleted."""
    files = list(title_files(title))
    row = Rendition.objects.filter(pk=rendition_id, media_file__in=files).first()
    if row is None:
        raise ProblemError(ErrorCode.NOT_FOUND, "No such rendition of this title.", status=404)
    if row.kind == RenditionKind.SOURCE:
        raise _conflict("The source plays as it is; remove the file from its library instead.")
    if row.kind == RenditionKind.HLS_VARIANT:
        raise _conflict("Delete the ladder (its master), not one rung.")
    if TranscodeJob.objects.filter(
        media_file_id=row.media_file_id, status__in=ACTIVE_JOB_STATUSES
    ).exists():
        raise _conflict("A job of this file is running; cancel it or wait for it.")
    scope = _DELETE_WITH.get(row.kind, Q(pk=row.pk))
    if row.kind == RenditionKind.HLS_MASTER and row.name != layout.HLS:
        scope = Q(pk=row.pk)
    with transaction.atomic():
        doomed = Rendition.objects.filter(scope, media_file_id=row.media_file_id)
        names = sorted(f"{r.kind}:{r.name}" for r in doomed)
        deleted, _ = doomed.delete()
        refresh_status_of_files([row.media_file_id])
        audit.record(
            "rendition.delete",
            actor=actor,
            target=row.media_file,
            before={"renditions": names},
            after=None,
            ip=ip,
        )
    _queue("publish_presentations", str(row.media_file_id))
    return deleted


# --- Reprocess ------------------------------------------------------------------------------

REPROCESSABLE: Final = (
    TranscodeProfile.COMPAT_MP4,
    TranscodeProfile.HLS,
    TranscodeProfile.UHD,
    TranscodeProfile.THUMBNAILS,
    TranscodeProfile.SUBTITLES,
)


def reprocess(
    title: Title,
    outputs: list[str],
    *,
    file_id: UUID | None,
    actor: User | None,
    ip: str | None,
) -> list[UUID]:
    """Queue the outputs again for the title's files (or one of them); returns the files.
    The worker probes each file and queues one job per output (`services.reprocess`)."""
    files = [_file_of(title, file_id)] if file_id is not None else list(title_files(title))
    if not files:
        raise _conflict("The title has no file on disk.")
    wanted = [o for o in REPROCESSABLE if o in outputs] if outputs else list(REPROCESSABLE)
    with transaction.atomic():
        audit.record(
            "title.reprocess",
            actor=actor,
            target=title.obj,
            after={"outputs": wanted, "files": [str(f.pk) for f in files]},
            ip=ip,
        )
        for file in files:
            if file.state != FileState.MATCHED:
                continue
            _queue("reprocess_media_file", str(file.pk), wanted)
    return [f.pk for f in files]


# --- Tracks ---------------------------------------------------------------------------------


def _track(title: Title, track_id: UUID) -> AudioTrack | SubtitleTrack:
    files = title_files(title)
    track: AudioTrack | SubtitleTrack | None = AudioTrack.objects.filter(
        pk=track_id, media_file__in=files
    ).first()
    if track is None:
        track = SubtitleTrack.objects.filter(pk=track_id, media_file__in=files).first()
    if track is None:
        raise ProblemError(ErrorCode.NOT_FOUND, "No such track of this title.", status=404)
    return track


def language_code(value: str) -> str:
    """An ISO 639-2 code from `ar`, `ara` or `ar-SA`; raises on anything else."""
    code = normalize_language(value)
    if code == "und" and value.strip().lower() != "und":
        raise _invalid("language", "Use an ISO 639 language code such as ar or eng.")
    return code


def _track_state(track: AudioTrack | SubtitleTrack) -> dict[str, Any]:
    return {
        "language": track.language,
        "title": track.title,
        "default": track.default,
        "forced": track.forced,
    }


def update_track(
    title: Title,
    track_id: UUID,
    changes: dict[str, Any],
    *,
    actor: User | None,
    ip: str | None,
) -> AudioTrack | SubtitleTrack:
    """Edit a track's language, title, default or forced flag. One default per kind
    and file: making a track default clears the others. The HLS masters are rewritten
    at once; the compat MP4 carries the change after a reprocess."""
    track = _track(title, track_id)
    before = _track_state(track)
    if "language" in changes:
        track.language = language_code(str(changes["language"]))
    if "title" in changes:
        track.title = str(changes["title"] or "")[:200]
    for flag in ("default", "forced"):
        if flag in changes:
            setattr(track, flag, bool(changes[flag]))
    model = type(track)
    with transaction.atomic():
        if track.default:
            model.objects.filter(media_file_id=track.media_file_id, default=True).exclude(
                pk=track.pk
            ).update(default=False)
        track.save()
        audit.record(
            "track.update",
            actor=actor,
            target=track,
            before=before,
            after=_track_state(track),
            ip=ip,
        )
    _queue("publish_presentations", str(track.media_file_id))
    return track


def add_subtitle(  # noqa: PLR0913
    title: Title,
    *,
    name: str,
    data: bytes,
    language: str,
    track_title: str,
    default: bool,
    forced: bool,
    file_id: UUID | None,
    actor: User | None,
    ip: str | None,
) -> SubtitleTrack:
    """Add an uploaded sidecar (SRT, ASS/SSA or WebVTT, any encoding); the subtitles
    job converts it and adds it to the HLS masters."""
    from apps.media import services  # noqa: PLC0415 (services import this package's tasks)

    file = _file_of(title, file_id)
    extension = Path(name).suffix.lower()
    fmt = subtitles.SIDECAR_EXTENSIONS.get(extension)
    if fmt is None:
        raise _invalid("file", "Upload an .srt, .ass, .ssa or .vtt file.")
    if len(data) > SUBTITLE_MAX_BYTES:
        raise _invalid("file", "The subtitle file is too large (4 MB at most).")
    code = language_code(language)
    try:
        decoded = subtitles.decode(data, code)
    except subtitles.SubtitleError:
        raise _invalid("file", "The file holds no readable subtitle text.") from None
    with transaction.atomic():
        if default:
            SubtitleTrack.objects.filter(media_file=file, default=True).update(default=False)
        track = SubtitleTrack.objects.create(
            media_file=file,
            external=True,
            upload=data,
            upload_name=Path(name).name[:255],
            codec=fmt.value,
            format=fmt,
            language=code,
            title=track_title[:200],
            default=default,
            forced=forced,
            encoding=decoded.encoding,
            source_hash=subtitles.content_hash(data),
            status=SubtitleStatus.PENDING,
        )
        audit.record(
            "track.upload",
            actor=actor,
            target=track,
            after={"language": code, "name": track.upload_name, "encoding": decoded.encoding},
            ip=ip,
        )
        services.ensure_output(file, TranscodeProfile.SUBTITLES)
    return track


def delete_subtitle(title: Title, track_id: UUID, *, actor: User | None, ip: str | None) -> None:
    """Remove an uploaded subtitle (embedded and sidecar tracks follow the files)."""
    track = _track(title, track_id)
    if not isinstance(track, SubtitleTrack) or track.upload is None:
        raise _conflict(
            "Only uploaded subtitles can be deleted; embedded and sidecar subtitles "
            "follow the files in the library."
        )
    file_id = track.media_file_id
    with transaction.atomic():
        audit.record("track.delete", actor=actor, target=track, before=_track_state(track), ip=ip)
        track.delete()
    _queue("publish_presentations", str(file_id))


# --- Images ---------------------------------------------------------------------------------


def _image_owner(title: Title) -> dict[str, Any]:
    return {title.kind: title.obj}


def title_images(title: Title) -> list[MediaImage]:
    return list(MediaImage.objects.filter(**_image_owner(title)).order_by("kind", "-is_primary"))


def image_alternatives(title: Title) -> tuple[list[dict[str, Any]], str | None]:
    """TMDB's posters, backdrops and logos (stills for an episode) that are not stored
    yet, best voted first; and an error code when TMDB could not be asked."""
    from apps.metadata import services as metadata  # noqa: PLC0415
    from apps.metadata.tmdb import TMDBError, image_url  # noqa: PLC0415

    if title.tmdb_id is None:
        return [], None
    try:
        client = metadata.tmdb()
        if title.kind == "movie":
            details = client.movie_details(title.tmdb_id)
        else:
            details = client.tv_details(title.tmdb_id)
    except TMDBError:
        return [], "provider_unavailable"
    images = details.get("images") if isinstance(details.get("images"), dict) else {}
    stored = set(
        MediaImage.objects.filter(**_image_owner(title)).values_list("source_path", flat=True)
    )
    groups = (("poster", "posters"), ("backdrop", "backdrops"), ("logo", "logos"))
    if title.kind == "episode":
        return [], None  # TMDB episode stills come with the season; the still is kept
    found: list[dict[str, Any]] = []
    for kind, key in groups:
        items = [i for i in (images or {}).get(key) or [] if isinstance(i, dict)]
        items.sort(key=lambda i: (-(i.get("vote_average") or 0), -(i.get("vote_count") or 0)))
        for item in items[:ALTERNATIVES_PER_KIND]:
            path = item.get("file_path")
            if not isinstance(path, str) or path in stored:
                continue
            found.append(
                {
                    "kind": kind,
                    "path": path,
                    "language": item.get("iso_639_1") or "",
                    "width": item.get("width") or 0,
                    "height": item.get("height") or 0,
                    "preview_url": image_url(path, "w185"),
                }
            )
    return found, None


def add_image(  # noqa: PLR0913
    title: Title,
    *,
    kind: str,
    tmdb_path: str | None,
    upload: bytes | None,
    primary: bool,
    actor: User | None,
    ip: str | None,
) -> None:
    """Queue storing an image: a TMDB alternative (by its path) or an upload."""
    if kind not in IMAGE_KINDS:
        raise _invalid("kind", "Choose poster, backdrop, logo or still.")
    if (tmdb_path is None) == (upload is None):
        raise _invalid("file", "Send either a TMDB image path or an image file.")
    if tmdb_path is not None and not (tmdb_path.startswith("/") and len(tmdb_path) < 200):
        raise _invalid("tmdb_path", "Not a TMDB image path.")
    if upload is not None and len(upload) > IMAGE_MAX_BYTES:
        raise _invalid("file", "The image is too large (15 MB at most).")
    with transaction.atomic():
        audit.record(
            "title.image_add",
            actor=actor,
            target=title.obj,
            after={"kind": kind, "tmdb_path": tmdb_path, "upload": upload is not None},
            ip=ip,
        )
        payload = base64.b64encode(upload).decode() if upload is not None else ""
        request = {
            "title_kind": title.kind,
            "title_id": str(title.id),
            "kind": kind,
            "tmdb_path": tmdb_path or "",
            "upload_b64": payload,
            "primary": primary,
        }
        _queue("store_title_image", request)


def set_primary_image(
    title: Title, image_id: UUID, *, actor: User | None, ip: str | None
) -> MediaImage:
    image = MediaImage.objects.filter(pk=image_id, **_image_owner(title)).first()
    if image is None:
        raise ProblemError(ErrorCode.NOT_FOUND, "No such image of this title.", status=404)
    with transaction.atomic():
        MediaImage.objects.filter(**_image_owner(title), kind=image.kind).update(is_primary=False)
        image.is_primary = True
        image.save(update_fields=["is_primary", "updated_at"])
        audit.record("title.image_primary", actor=actor, target=image, ip=ip)
    _announce(title)
    return image


def delete_image(title: Title, image_id: UUID, *, actor: User | None, ip: str | None) -> None:
    image = MediaImage.objects.filter(pk=image_id, **_image_owner(title)).first()
    if image is None:
        raise ProblemError(ErrorCode.NOT_FOUND, "No such image of this title.", status=404)
    with transaction.atomic():
        audit.record(
            "title.image_delete",
            actor=actor,
            target=image,
            before={"kind": image.kind, "primary": image.is_primary},
            ip=ip,
        )
        was_primary, kind = image.is_primary, image.kind
        image.delete()
        if was_primary:
            successor = (
                MediaImage.objects.filter(**_image_owner(title), kind=kind)
                .order_by("-created_at")
                .first()
            )
            if successor is not None:
                successor.is_primary = True
                successor.save(update_fields=["is_primary", "updated_at"])
    _announce(title)


def _announce(title: Title) -> None:
    if title.kind == "movie":
        notify_catalog_changed("movie", [title.id])
    else:
        series_id = title.id if title.kind == "series" else title.obj.season.series_id  # type: ignore[union-attr]
        notify_catalog_changed("series", [series_id])


# --- Rematch --------------------------------------------------------------------------------


def rematch(
    title: Title,
    *,
    tmdb_id: int | None,
    kind: str | None,
    actor: User | None,
    ip: str | None,
) -> dict[str, Any]:
    """Link every file of the title to the TMDB movie or series `tmdb_id` (episodes are
    placed by their season and episode numbers), or, without an id, match them again
    automatically (they may land in the review queue)."""
    from apps.library.parsing import ParseResult  # noqa: PLC0415
    from apps.metadata import services as metadata  # noqa: PLC0415
    from apps.metadata.tmdb import TMDBError  # noqa: PLC0415

    files = list(title_files(title))
    if not files:
        raise _conflict("The title has no file on disk.")
    target_kind = kind or ("movie" if title.kind == "movie" else "tv")
    if target_kind not in ("movie", "tv"):
        raise _invalid("kind", "Choose movie or tv.")
    if tmdb_id is None:
        with transaction.atomic():
            for file in files:
                file.state = FileState.MATCHING
                file.save(update_fields=["state", "updated_at"])
                _queue_match(file.pk)
            audit.record(
                "title.rematch",
                actor=actor,
                target=title.obj,
                after={"automatic": True, "files": len(files)},
                ip=ip,
            )
        return {"files": len(files), "automatic": True, "title_ids": []}
    client = metadata.tmdb()
    titles: set[UUID] = set()
    try:
        for file in files:
            if target_kind == "movie":
                titles.add(metadata.accept_movie(file, tmdb_id, 1.0, client, actor=actor).pk)
            else:
                parsed = ParseResult.from_json(file.parse_result)
                titles.add(
                    metadata.accept_episode(file, tmdb_id, parsed, 1.0, client, actor=actor).pk
                )
    except metadata.PlacementError as exc:
        raise _invalid("tmdb_id", str(exc)) from None
    except TMDBError:
        raise ProblemError(
            ErrorCode.PROVIDER_UNAVAILABLE, "TMDB is unavailable; try again shortly."
        ) from None
    audit.record(
        "title.rematch",
        actor=actor,
        target=title.obj,
        after={"kind": target_kind, "tmdb_id": tmdb_id, "files": len(files)},
        ip=ip,
    )
    return {"files": len(files), "automatic": False, "title_ids": sorted(titles)}


def _queue_match(file_id: UUID) -> None:
    from apps.metadata import tasks  # noqa: PLC0415

    transaction.on_commit(lambda: tasks.match_media_file.delay(str(file_id)))
