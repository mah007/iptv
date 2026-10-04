"""What each transcode job produces (SPEC §7.3; ADR-0010, ADR-0014).

A runner gets a `JobContext` (the job, the probed source, its asset folder and a
private `.tmp-<job>` folder in it), runs ffmpeg with progress and cancellation,
verifies the output, moves it into place atomically and records the renditions.
`apps.media.services.run_job` owns the lease, retries and the job row's status.

- `compat_mp4`: the progressive MP4 every client plays, with sidecar subtitles muxed
  in as mov_text (IPTV apps show them);
- `hls`: the SDR ladder, one decode split into every rung, plus one AAC rendition per
  audio language (and an E-AC-3 copy when the source has one);
- `uhd`: the UHD version (HEVC Main10, or the source itself when it already fits) and
  its HLS rung;
- `thumbnails`: scrubbing sprites with `thumbs.vtt`, and a poster or still from a frame
  when the provider has none;
- `subtitles`: embedded text subtitles extracted, sidecars and uploads decoded and
  converted, all to UTF-8 WebVTT and SRT.
"""

from __future__ import annotations

import io
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

import structlog
from django.db import transaction
from django.utils import timezone
from PIL import Image

from apps.catalog.models import ImageKind, ImageSource, MediaFile, MediaImage
from apps.catalog.services import refresh_status_of_files
from apps.catalog.signals import notify_catalog_changed
from apps.media import ffmpeg, layout, presentations, subtitles
from apps.media.ffmpeg import Encoding, ExtraSubtitle
from apps.media.hls import playlist_bitrates, subtitle_playlist, video_codec_string
from apps.media.models import (
    Rendition,
    RenditionKind,
    RenditionStatus,
    SubtitleFormat,
    SubtitleStatus,
    SubtitleTrack,
    TranscodeJob,
    TranscodeProfile,
)
from apps.media.planner import (
    CompatMp4Plan,
    HlsPlan,
    PlanError,
    ProcessingPlan,
    ProcessingPolicy,
    SubtitlePlan,
    UhdMode,
    plan_processing,
)
from apps.media.probe import HdrKind, ProbeResult, probe
from apps.media.profiles import Backend, Profiles, VideoCodec
from apps.media.progress import ProgressUpdate
from apps.media.verify import (
    VerificationError,
    expected_compat,
    expected_uhd,
    verify_file,
    verify_hls,
    verify_media_playlist,
)

logger = structlog.get_logger(__name__)

#: The compat MP4 and the HLS ladder never exceed 1080p (profiles.yaml's boxes).
SDR_MAX_QUALITY: Final = 1080
UHD_MAX_QUALITY: Final = 2160
#: HDR kinds as HLS VIDEO-RANGE values.
VIDEO_RANGES: Final = {
    HdrKind.SDR: "SDR",
    HdrKind.HDR10: "PQ",
    HdrKind.DV: "PQ",
    HdrKind.HLG: "HLG",
}
POSTER_ASPECT: Final = 2 / 3
#: Sprites decode key frames only from inputs at least this long (a 2 s GOP compat MP4).
KEYFRAME_SPRITES_MIN_MS: Final = 120_000
FRAME_SOURCE_PATH: Final = "frame"


@dataclass(slots=True)
class JobContext:
    job: TranscodeJob
    file: MediaFile
    source: Path
    probe: ProbeResult
    profiles: Profiles
    workdir: Path  # the asset folder
    temporary: Path  # `.tmp-<job>` inside it (same filesystem: renames are atomic)
    device: str | None
    on_progress: Callable[[ProgressUpdate], None]
    cancel: threading.Event
    timeout_s: int

    def encoding(self) -> Encoding:
        return Encoding(self.profiles, Backend(self.job.backend), device=self.device)

    def run(self, command: ffmpeg.Command) -> ffmpeg.RunResult:
        return ffmpeg.run(
            command, on_progress=self.on_progress, timeout_s=self.timeout_s, cancel=self.cancel
        )

    def plan(self, max_quality: int = SDR_MAX_QUALITY) -> ProcessingPlan:
        return plan_processing(
            self.probe, policy=ProcessingPolicy.INGEST, max_quality=max_quality,
            profiles=self.profiles,
        )  # fmt: skip

    def set_encoder(self, encoder: str, *, remux: bool | None = None) -> None:
        self.job.encoder = encoder[:32]
        fields = ["encoder", "updated_at"]
        if remux is not None:
            self.job.remux = remux
            fields.append("remux")
        self.job.save(update_fields=fields)


def _rendition(ctx: JobContext) -> Rendition:
    if ctx.job.rendition_id is None:
        raise ValueError("job without a rendition")
    return Rendition.objects.get(pk=ctx.job.rendition_id)


def _mark_ready(row: Rendition, **values: Any) -> None:
    now = timezone.now()
    for name, value in values.items():
        setattr(row, name, value)
    row.status = RenditionStatus.READY
    row.error = ""
    row.ready_at = now
    row.save()


# --- compat MP4 ---------------------------------------------------------------------------


def run_compat(ctx: JobContext) -> None:
    compat: CompatMp4Plan | None = ctx.plan().compat_mp4
    if compat is None:
        raise PlanError("no_compat_plan")
    extras = _compat_sidecars(ctx)
    encoding = ctx.encoding()
    output = ctx.temporary / f"{layout.COMPAT}.mp4"
    command = ffmpeg.compat_mp4_command(
        ctx.probe, compat, source=ctx.source, output=output, encoding=encoding,
        extra_subtitles=extras,
    )  # fmt: skip
    encoder = "copy" if compat.is_remux else encoding.preset(VideoCodec.H264).encoder
    ctx.set_encoder(encoder, remux=compat.is_remux)
    ctx.run(command)
    expected = expected_compat(compat, ctx.probe.duration_ms)
    made = verify_file(output, replace(expected, subtitle=expected.subtitle + len(extras)))
    os.replace(output, ctx.workdir / f"{layout.COMPAT}.mp4")
    video = made.video
    with transaction.atomic():
        _mark_ready(
            _rendition(ctx),
            name=layout.COMPAT,
            width=video.width if video else None,
            height=video.height if video else None,
            codec=video.codec if video else "",
            bitrate=made.bitrate,
            size=made.size or 0,
            duration_s=made.duration_ms / 1000 if made.duration_ms else None,
            encoder_used=ctx.job.encoder,
            source_hash=ctx.file.xxhash64,
            details={"remux": compat.is_remux, "subtitles": len(compat.subtitles) + len(extras)},
        )
        refresh_status_of_files([ctx.file.pk])
    presentations.publish(ctx.file)


def _compat_sidecars(ctx: JobContext) -> list[ExtraSubtitle]:
    """Sidecars and uploads as UTF-8 files the compat MP4 muxes in. A subtitle that
    cannot be read is left out (its track shows why once the subtitles job ran)."""
    folder = ctx.temporary / "sidecars"
    extras: list[ExtraSubtitle] = []
    tracks = (
        SubtitleTrack.objects.filter(media_file=ctx.file, external=True)
        .exclude(format__in=(SubtitleFormat.PGS, SubtitleFormat.VOBSUB))
        .exclude(status=SubtitleStatus.FAILED)
    )
    for n, track in enumerate(tracks.order_by("created_at"), start=1):
        try:
            data, extension = _external_bytes(ctx.file, track)
            decoded = subtitles.decode(data, track.language)
        except (OSError, subtitles.SubtitleError) as exc:
            logger.warning("media.sidecar_skipped", track=str(track.pk), error=str(exc)[:80])
            continue
        if "-->" not in decoded.text:
            logger.warning("media.sidecar_skipped", track=str(track.pk), error="no_cues")
            continue
        path = folder / f"{n}{extension}"
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(decoded.text, encoding="utf-8")
        extras.append(
            ExtraSubtitle(
                path=path,
                language=track.language,
                title=track.title or None,
                default=track.default,
                forced=track.forced,
            )
        )
    return extras


def _external_bytes(file: MediaFile, track: SubtitleTrack) -> tuple[bytes, str]:
    """A sidecar's or upload's bytes and its file extension (`.srt`)."""
    if track.upload is not None:
        data = bytes(track.upload)
        extension = Path(track.upload_name).suffix.lower() or f".{track.format}"
        return data, extension
    path = (Path(file.library.path) / track.sidecar_path).resolve()
    if not path.is_relative_to(Path(file.library.path).resolve()):
        raise subtitles.SubtitleError("outside_library")
    return subtitles.read_limited(path), path.suffix.lower()


# --- HLS ladder ---------------------------------------------------------------------------


def run_hls(ctx: JobContext) -> None:
    plan: HlsPlan | None = ctx.plan().hls
    if plan is None or not plan.rungs:
        raise PlanError("no_hls_plan")
    plan = replace(plan, subtitles=())  # subtitles come from the tracks (subs/)
    encoding = ctx.encoding()
    out = ctx.temporary / layout.HLS
    command = ffmpeg.hls_command(ctx.probe, plan, output_dir=out, source=ctx.source,
                                 encoding=encoding)  # fmt: skip
    ctx.set_encoder(encoding.preset(VideoCodec.H264).encoder, remux=False)
    ctx.run(command)
    ffmpeg.write_hls_master(plan, out, duration_ms=ctx.probe.duration_ms)
    verify_hls(out, expected_duration_ms=ctx.probe.duration_ms)
    rungs = [_rung_facts(ctx, plan, i, out) for i in range(len(plan.rungs))]
    audio = [_audio_facts(plan, i, out, ctx.probe) for i in range(len(plan.audio))]
    shared = layout.tree_size(out) - sum(r["size"] for r in rungs)
    layout.replace_dir(out, ctx.workdir / layout.HLS)
    with transaction.atomic():
        Rendition.objects.filter(
            media_file=ctx.file, kind=RenditionKind.HLS_VARIANT, name__startswith="v"
        ).delete()
        for rung in rungs:
            Rendition.objects.create(
                media_file=ctx.file,
                kind=RenditionKind.HLS_VARIANT,
                name=rung["name"],
                storage_key=layout.asset_key(ctx.file),
                container="m4s",
                width=rung["width"],
                height=rung["height"],
                bitrate=rung["average_bps"],
                codec="h264",
                size=rung["size"],
                status=RenditionStatus.READY,
                encoder_used=ctx.job.encoder,
                duration_s=_seconds(ctx.probe),
                ready_at=timezone.now(),
                source_hash=ctx.file.xxhash64,
                details={k: rung[k] for k in _RUNG_DETAILS},
            )
        top = max(rungs, key=lambda r: r["box_height"])
        _mark_ready(
            _rendition(ctx),
            name=layout.HLS,
            container="m3u8",
            width=top["width"],
            height=top["box_height"],
            codec="h264",
            bitrate=top["average_bps"],
            size=shared,
            duration_s=_seconds(ctx.probe),
            encoder_used=ctx.job.encoder,
            source_hash=ctx.file.xxhash64,
            details={"audio": audio, "segment_s": plan.segment_s, "rungs": len(rungs)},
        )
    presentations.publish(ctx.file)


_RUNG_DETAILS: Final = (
    "box_height",
    "codecs",
    "peak_bps",
    "average_bps",
    "frame_rate",
    "video_range",
    "default",
    "rung",
)


def _rung_facts(ctx: JobContext, plan: HlsPlan, position: int, out: Path) -> dict[str, Any]:
    rung = plan.rungs[position]
    folder = out / layout.rung_dir(position)
    peak, average = playlist_bitrates(folder / layout.PLAYLIST)
    configured = ctx.profiles.hls.rung(rung.name)
    return {
        "name": layout.rung_dir(position),
        "rung": rung.name,
        "width": rung.width,
        "height": rung.height,
        "box_height": configured.height if configured and not rung.native else rung.height,
        "codecs": video_codec_string(folder / layout.INIT, "h264"),
        "peak_bps": peak,
        "average_bps": average,
        "frame_rate": round(float(rung.video.frame_rate), 3) if rung.video.frame_rate else None,
        "video_range": "SDR",
        "default": rung.default,
        "size": layout.tree_size(folder),
    }


def _audio_facts(plan: HlsPlan, position: int, out: Path, source: ProbeResult) -> dict[str, Any]:
    audio = plan.audio[position]
    folder = out / layout.audio_dir(position)
    channels = {track.index: track.channels for track in source.audio}
    peak, average = playlist_bitrates(folder / layout.PLAYLIST)
    return {
        "dir": layout.audio_dir(position),
        "group": audio.group,
        "language": audio.language,
        "name": audio.name,
        "default": audio.default,
        "channels": audio.channels,
        "codec": audio.codec,
        "source_index": audio.source_index,
        "source_channels": channels.get(audio.source_index, audio.channels),
        "peak_bps": peak,
        "average_bps": average,
    }


def _seconds(result: ProbeResult) -> float | None:
    return result.duration_ms / 1000 if result.duration_ms else None


# --- UHD ------------------------------------------------------------------------------------


def run_uhd(ctx: JobContext) -> None:
    plan = ctx.plan(UHD_MAX_QUALITY)
    uhd = plan.uhd
    if uhd is None or plan.compat_mp4 is None:
        raise PlanError("not_uhd")
    workdir = ctx.workdir
    video = ctx.probe.video
    if video is None:
        raise PlanError("no_video_stream")
    if uhd.mode is UhdMode.KEEP_SOURCE:
        extension = ctx.source.suffix.lstrip(".").lower()
        progressive = workdir / f"{layout.UHD}.{extension}"
        layout.link(str(ctx.source), progressive)
        ctx.set_encoder("copy", remux=True)
        segment_input, video_index, codec = ctx.source, video.index, video.codec
        made = ctx.probe
        linked = True
    else:
        encoding = ctx.encoding()
        output = ctx.temporary / f"{layout.UHD}.mp4"
        audio = plan.compat_mp4.audio
        command = ffmpeg.uhd_command(ctx.probe, uhd, audio, source=ctx.source, output=output,
                                     encoding=encoding)  # fmt: skip
        ctx.set_encoder(encoding.preset(VideoCodec.HEVC).encoder, remux=False)
        ctx.run(command)
        made = verify_file(output, expected_uhd(uhd, audio, ctx.probe.duration_ms))
        progressive = workdir / f"{layout.UHD}.mp4"
        os.replace(output, progressive)
        segment_input, video_index, codec = progressive, 0, uhd.video.codec
        linked = False
    for stale in workdir.glob(f"{layout.UHD}.*"):
        if stale != progressive:
            layout.remove(stale)
    rung_out = ctx.temporary / "uhd-rung"
    command = ffmpeg.rung_segment_command(
        segment_input, video_index=video_index, codec=codec, output_dir=rung_out,
        segment_s=ctx.profiles.hls.segment_s, duration_ms=ctx.probe.duration_ms,
    )  # fmt: skip
    ctx.run(command)
    verify_media_playlist(rung_out, expected_duration_ms=ctx.probe.duration_ms)
    peak, average = playlist_bitrates(rung_out / layout.PLAYLIST)
    codecs = video_codec_string(rung_out / layout.INIT, codec)
    rung_size = layout.tree_size(rung_out)
    presentation = workdir / layout.presentation(layout.UHD_CEILING)
    presentation.mkdir(parents=True, exist_ok=True)
    layout.replace_dir(rung_out, presentation / layout.UHD_RUNG)
    made_video = made.video or video
    video_range = VIDEO_RANGES.get(video.hdr, "SDR")
    rate = round(float(made_video.frame_rate), 3) if made_video.frame_rate else None
    with transaction.atomic():
        _mark_ready(
            _rendition(ctx),
            name=layout.UHD,
            container=progressive.suffix.lstrip("."),
            width=made_video.width,
            height=made_video.height,
            codec=made_video.codec,
            bitrate=made.bitrate,
            size=(ctx.probe.size or ctx.file.size) if linked else (made.size or 0),
            duration_s=_seconds(made),
            encoder_used=ctx.job.encoder,
            source_hash=ctx.file.xxhash64,
            details={"mode": uhd.mode.value, "linked": linked, "video_range": video_range},
        )
        Rendition.objects.update_or_create(
            media_file=ctx.file,
            kind=RenditionKind.HLS_VARIANT,
            name=layout.UHD_RUNG,
            defaults={
                "storage_key": layout.asset_key(ctx.file),
                "container": "m4s",
                "width": made_video.width,
                "height": made_video.height,
                "bitrate": average,
                "codec": made_video.codec,
                "size": rung_size,
                "status": RenditionStatus.READY,
                "error": "",
                "encoder_used": ctx.job.encoder,
                "duration_s": _seconds(made),
                "ready_at": timezone.now(),
                "source_hash": ctx.file.xxhash64,
                "details": {
                    "box_height": UHD_MAX_QUALITY,
                    "codecs": codecs,
                    "peak_bps": peak,
                    "average_bps": average,
                    "frame_rate": rate,
                    "video_range": video_range,
                    "default": False,
                },
            },
        )
    presentations.publish(ctx.file)


# --- Thumbnails -----------------------------------------------------------------------------


def _thumbnail_input(ctx: JobContext) -> tuple[Path, ProbeResult, bool]:
    """The compat MP4 when there is one (SDR, 2 s GOPs: key frames are enough), else
    the source (a direct-play source is SDR already)."""
    compat = ctx.workdir / f"{layout.COMPAT}.mp4"
    ready = Rendition.objects.filter(
        media_file=ctx.file, kind=RenditionKind.COMPAT_MP4, status=RenditionStatus.READY
    ).exists()
    if ready and compat.is_file():
        return compat, probe(compat), True
    return ctx.source, ctx.probe, False


def run_thumbnails(ctx: JobContext) -> None:
    source, result, keyframes_only = _thumbnail_input(ctx)
    plan = plan_processing(
        result, policy=ProcessingPolicy.PASSTHROUGH, max_quality=SDR_MAX_QUALITY,
        profiles=ctx.profiles,
    ).thumbnails  # fmt: skip
    duration_ms = result.duration_ms or ctx.file.duration_ms or 0
    out = ctx.temporary / layout.THUMBS
    ctx.set_encoder("mjpeg", remux=False)
    # Key frames alone are plenty for a film; a short clip may hold a single one.
    keyframes_only = keyframes_only and duration_ms >= KEYFRAME_SPRITES_MIN_MS
    for decode_keyframes_only in dict.fromkeys((keyframes_only, False)):
        command = ffmpeg.sprite_command(
            plan, source=source, output_dir=out, profiles=ctx.profiles, duration_ms=duration_ms,
            keyframes_only=decode_keyframes_only,
        )  # fmt: skip
        ctx.run(command)
        if any(out.glob("sprite_*.jpg")):
            break
    sheets = sorted(out.glob("sprite_*.jpg"))
    if not sheets or duration_ms <= 0:
        raise VerificationError(["no_sprites"])
    vtt = _thumbnails_vtt(plan, duration_ms, len(sheets))
    layout.write_text(out / layout.THUMBS_VTT, vtt)
    size = layout.tree_size(out)
    layout.replace_dir(out, ctx.workdir / layout.THUMBS)
    with transaction.atomic():
        _mark_ready(
            _rendition(ctx),
            name=layout.THUMBS,
            container="vtt",
            width=plan.tile_width,
            height=plan.tile_height,
            codec="mjpeg",
            size=size,
            duration_s=duration_ms / 1000,
            encoder_used="mjpeg",
            source_hash=ctx.file.xxhash64,
            details={
                "sheets": len(sheets),
                "tiles": vtt.count("#xywh="),
                "interval_s": plan.interval_s,
                "columns": plan.columns,
                "rows": plan.rows,
            },
        )
    _frame_artwork(ctx, source, plan)
    presentations.publish(ctx.file)


def _thumbnails_vtt(plan: Any, duration_ms: int, sheets: int) -> str:
    """`thumbs.vtt` without cues on sheets ffmpeg did not write (a last partial tile)."""
    text = ffmpeg.thumbnails_vtt(plan, duration_ms)
    kept: list[str] = []
    blocks = text.split("\n\n")
    for block in blocks:
        names = [line for line in block.splitlines() if "#xywh=" in line]
        if names and int(names[0].split("_")[1].split(".")[0]) > sheets:
            continue
        kept.append(block)
    return "\n\n".join(kept).rstrip("\n") + "\n"


def _frame_artwork(ctx: JobContext, source: Path, plan: Any) -> None:
    """A poster (movies) or still (episodes) from a frame at 10% when the metadata
    provider has none, so every title has a picture (SPEC §7.3)."""
    file = ctx.file
    movie = file.movie
    wants_poster = (
        movie is not None
        and not MediaImage.objects.filter(movie=movie, kind=ImageKind.POSTER).exists()
    )
    episodes = [
        e for e in file.episodes.all()
        if not MediaImage.objects.filter(episode=e, kind=ImageKind.STILL).exists()
    ]  # fmt: skip
    if not wants_poster and not episodes:
        return
    frame = ctx.temporary / "frame.jpg"
    try:
        ffmpeg.run(
            ffmpeg.poster_command(plan, source=source, output=frame, profiles=ctx.profiles),
            timeout_s=300,
        )
        data = frame.read_bytes()
    except (ffmpeg.FfmpegError, OSError) as exc:
        logger.warning("media.frame_artwork_failed", file=str(file.pk), error=str(exc)[:120])
        return
    from apps.metadata import images  # noqa: PLC0415 (metadata imports catalog services)
    from apps.metadata.services import image_store  # noqa: PLC0415

    store = image_store()
    if wants_poster and movie is not None:
        stored = images.store_image(
            _portrait(data), owner=f"movie/{movie.pk}", kind="poster", store=store
        )
        _record_image(stored, kind=ImageKind.POSTER, movie=movie)
        notify_catalog_changed("movie", [movie.pk])
    for episode in episodes:
        stored = images.store_image(data, owner=f"episode/{episode.pk}", kind="still", store=store)
        _record_image(stored, kind=ImageKind.STILL, episode=episode)
    if episodes:
        series_ids = {e.season.series_id for e in episodes}
        notify_catalog_changed("series", list(series_ids))


def _portrait(data: bytes) -> bytes:
    """The middle 2:3 of a frame, as a poster."""
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        target = min(width, round(height * POSTER_ASPECT))
        left = (width - target) // 2
        cropped = image.convert("RGB").crop((left, 0, left + target, height))
        buffer = io.BytesIO()
        cropped.save(buffer, format="JPEG", quality=90)
        return buffer.getvalue()


def _record_image(stored: Any, *, kind: str, **owner: Any) -> None:
    with transaction.atomic():
        MediaImage.objects.filter(**owner, kind=kind).update(is_primary=False)
        MediaImage.objects.create(
            **owner,
            kind=kind,
            sizes=stored.keys(),
            width=stored.width,
            height=stored.height,
            blurhash=stored.blurhash,
            source=ImageSource.UPLOAD,
            source_path=FRAME_SOURCE_PATH,
            is_primary=True,
        )


# --- Subtitles ------------------------------------------------------------------------------


def run_subtitles(ctx: JobContext) -> None:
    file = ctx.file
    tracks = list(SubtitleTrack.objects.filter(media_file=file, status=SubtitleStatus.PENDING))
    subs_dir = ctx.workdir / layout.SUBS
    work = ctx.temporary / layout.SUBS
    duration_s = (ctx.probe.duration_ms or file.duration_ms or 0) / 1000
    ctx.set_encoder("webvtt", remux=False)
    embedded = [t for t in tracks if t.stream_index is not None and t.is_text]
    if embedded:
        _extract_embedded(ctx, embedded, work, subs_dir, duration_s)
    for track in tracks:
        if track.external and track.is_text:
            _convert_external(ctx, track, work, subs_dir, duration_s)
        elif not track.is_text:
            track.status = SubtitleStatus.UNSUPPORTED
            track.save(update_fields=["status", "updated_at"])
    presentations.publish(file)


def _extract_embedded(
    ctx: JobContext,
    tracks: list[SubtitleTrack],
    work: Path,
    subs_dir: Path,
    duration_s: float,
) -> None:
    streams = {s.index: s for s in ctx.probe.subtitles}
    plans = []
    for track in tracks:
        stream = streams.get(track.stream_index or -1)
        if stream is None or not stream.is_text:
            track.status, track.error = SubtitleStatus.FAILED, "stream_missing"
            track.save(update_fields=["status", "error", "updated_at"])
            continue
        plans.append(
            SubtitlePlan(
                source_index=stream.index, codec=stream.codec, language=stream.language,
                title=stream.title, forced=stream.forced, default=stream.default,
                hearing_impaired=stream.hearing_impaired, is_text=True,
                extract_formats=("vtt", "srt"), burn_in_candidate=False,
            )
        )  # fmt: skip
    if not plans:
        return
    ctx.run(ffmpeg.subtitles_command(plans, source=ctx.source, output_dir=work))
    by_index = {t.stream_index: t for t in tracks}
    for plan in plans:
        track = by_index[plan.source_index]
        key = f"{plan.source_index}.{plan.language}"
        vtt = ffmpeg.subtitle_path(work, plan, "vtt")
        srt = ffmpeg.subtitle_path(work, plan, "srt")
        _publish_track(
            track, key, vtt=vtt, srt=srt, subs_dir=subs_dir, duration_s=duration_s, encoding=""
        )


def _convert_external(
    ctx: JobContext, track: SubtitleTrack, work: Path, subs_dir: Path, duration_s: float
) -> None:
    try:
        data, extension = _external_bytes(ctx.file, track)
        decoded = subtitles.decode(data, track.language)
    except (OSError, subtitles.SubtitleError) as exc:
        code = str(exc) if isinstance(exc, subtitles.SubtitleError) else "unreadable"
        track.status, track.error = SubtitleStatus.FAILED, code[:200]
        track.save(update_fields=["status", "error", "updated_at"])
        return
    key = track.storage_key or subtitles.next_external_key(ctx.file, track.language)
    work.mkdir(parents=True, exist_ok=True)
    utf8 = work / f"{key}.in{extension}"
    utf8.write_text(decoded.text, encoding="utf-8")
    vtt, srt = work / f"{key}.vtt", work / f"{key}.srt"
    try:
        ffmpeg.run(ffmpeg.subtitle_convert_command(utf8, vtt=vtt, srt=srt), timeout_s=120)
    except ffmpeg.FfmpegError:
        track.status, track.error = SubtitleStatus.FAILED, "convert"
        track.save(update_fields=["status", "error", "updated_at"])
        return
    track.source_hash = subtitles.content_hash(data)
    _publish_track(
        track,
        key,
        vtt=vtt,
        srt=srt,
        subs_dir=subs_dir,
        duration_s=duration_s,
        encoding=decoded.encoding,
    )


def _publish_track(  # noqa: PLR0913
    track: SubtitleTrack,
    key: str,
    *,
    vtt: Path,
    srt: Path,
    subs_dir: Path,
    duration_s: float,
    encoding: str,
) -> None:
    text = vtt.read_text(encoding="utf-8") if vtt.is_file() else ""
    cues = subtitles.count_cues(text)
    if cues == 0:
        track.status, track.error, track.cues = SubtitleStatus.FAILED, "no_cues", 0
        track.save(update_fields=["status", "error", "cues", "updated_at"])
        return
    subs_dir.mkdir(parents=True, exist_ok=True)
    os.replace(vtt, subs_dir / f"{key}.vtt")
    if srt.is_file():
        os.replace(srt, subs_dir / f"{key}.srt")
    layout.write_text(subs_dir / f"{key}.m3u8", subtitle_playlist(f"{key}.vtt", duration_s))
    track.storage_key = key
    track.status = SubtitleStatus.READY
    track.error = ""
    track.cues = cues
    if encoding:
        track.encoding = encoding
    track.save()


RUNNERS: Final[dict[str, Callable[[JobContext], None]]] = {
    TranscodeProfile.COMPAT_MP4: run_compat,
    TranscodeProfile.HLS: run_hls,
    TranscodeProfile.UHD: run_uhd,
    TranscodeProfile.THUMBNAILS: run_thumbnails,
    TranscodeProfile.SUBTITLES: run_subtitles,
}
