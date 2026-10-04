# ruff: noqa: RUF001 (Arabic and Cyrillic subtitle text)
"""Every output on real ffmpeg (SPEC §7.3, ADR-0014): subtitles (embedded and a
Windows-1256 Arabic sidecar), the compat MP4 with the sidecar muxed in, thumbnails with a
poster from a frame, the HLS ladder with its presentations, the UHD version and its rung,
and what playback then offers per plan quality."""

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pytest_django import Settings

from apps.catalog.models import FileState, ImageKind, ImageSource, MediaFile, MediaImage, Movie
from apps.catalog.playable import playable_title
from apps.core.services import reset_settings_cache, set_setting
from apps.core.stores import state_redis
from apps.library.models import Library
from apps.media import layout, presentations, services, tasks
from apps.media.models import (
    AudioTrack,
    JobStatus,
    Rendition,
    RenditionKind,
    RenditionStatus,
    SubtitleStatus,
    SubtitleTrack,
    TranscodeJob,
    TranscodeProfile,
)
from apps.media.probe import probe
from apps.media.services import Prepared
from apps.media.verify import parse_master_playlist, verify_media_playlist
from apps.playback import tokens
from apps.playback.models import TitleKind
from apps.playback.services import RenditionKind as Kind
from apps.playback.tests.conftest import MEDIA_BASE_URL, VECTORS

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="ffmpeg/ffprobe not installed",
    ),
]

ARABIC_SRT = (
    "1\r\n00:00:00,200 --> 00:00:01,000\r\nهذا مقطع اختبار مولَّد بواسطة FFmpeg.\r\n\r\n"
    "2\r\n00:00:01,100 --> 00:00:01,900\r\nشُكْرًا لِلمُشاهَدة!\r\n"
)
ENGLISH_SRT = "1\n00:00:00,500 --> 00:00:01,500\nHello there.\n"


@pytest.fixture(autouse=True)
def _isolated_settings_cache() -> Iterator[None]:
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    state_redis().flushdb()
    yield
    state_redis().flushdb()


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks.prepare_media_file, "delay", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks.run_transcode_job, "apply_async", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks.publish_presentations, "delay", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "apps.media.services.transaction.on_commit", lambda callback, **_kw: callback()
    )


@pytest.fixture
def media_keys(settings: Settings, tmp_path: Path) -> Iterator[None]:
    keys = json.loads(VECTORS.read_text(encoding="utf-8"))["keys"]
    path = tmp_path / "media_token_keys.json"
    path.write_text(json.dumps(keys), encoding="utf-8")
    settings.MEDIA_TOKEN_KEYS_FILE = str(path)
    settings.MEDIA_BASE_URL = MEDIA_BASE_URL
    tokens.reset_keyring()
    yield
    tokens.reset_keyring()


def _ffmpeg(*args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv
        ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", *args],  # noqa: S607
        check=True,
        timeout=120,
    )


@pytest.fixture
def library(make_library: Callable[..., Library], data_root: Path) -> Library:
    return make_library()


def film(library: Library, folder: str = "Film (2020)") -> MediaFile:
    """A 720p H.264 + AAC MKV (a remux) with an English SRT track, and an Arabic
    sidecar in Windows-1256 with CRLF line ends next to it."""
    root = Path(library.path) / folder
    root.mkdir(parents=True)
    english = root.parent / "english.srt"
    english.write_text(ENGLISH_SRT, encoding="utf-8")
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-i", str(english),
        "-map", "0:v", "-map", "1:a", "-map", "2:s",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "50",
        "-c:a", "aac", "-c:s", "srt",
        "-metadata:s:a:0", "language=eng", "-metadata:s:s:0", "language=eng",
        str(root / "Film.2020.mkv"),
    )  # fmt: skip
    (root / "Film.2020.ar.srt").write_bytes(ARABIC_SRT.encode("cp1256"))
    movie = Movie.objects.create(title="Film")
    return MediaFile.objects.create(
        library=library,
        storage_key=f"{folder}/Film.2020.mkv",
        movie=movie,
        state=FileState.MATCHED,
        xxhash64="0011223344556677",
        duration_ms=2000,
    )


def uhd_source(library: Library, *, codec: str = "libx265") -> MediaFile:
    """A short 2560x1440 clip: HEVC (kept as the UHD version) or H.264 (needs an encode)."""
    path = Path(library.path) / f"UHD.{codec}.mkv"
    args = ["-x265-params", "log-level=error"] if codec == "libx265" else []
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=2560x1440:rate=25:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-c:v", codec, "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "25", *args,
        "-c:a", "aac", "-metadata:s:a:0", "language=eng", str(path),
    )  # fmt: skip
    return MediaFile.objects.create(
        library=library,
        storage_key=path.name,
        movie=Movie.objects.create(title="Wide"),
        state=FileState.MATCHED,
        xxhash64="8899aabbccddeeff",
        duration_ms=1000,
    )


def run(file: MediaFile, profile: str) -> TranscodeJob:
    job = TranscodeJob.objects.get(
        media_file=file, profile=profile, status__in=(JobStatus.QUEUED, JobStatus.RUNNING)
    )
    status = services.run_job(job.pk, job.dispatches, host="t1")
    job.refresh_from_db()
    assert status is JobStatus.DONE, (job.error, job.error_tail)
    return job


def asset(file: MediaFile) -> Path:
    return layout.asset_dir(file.pk.hex)


def test_a_film_gets_every_output(  # noqa: PLR0915 (one walk through every output)
    library: Library, media_keys: None
) -> None:
    file = film(library)

    assert services.prepare_file(file.pk) is Prepared.QUEUED
    queued = set(TranscodeJob.objects.filter(media_file=file).values_list("profile", flat=True))
    assert queued == {"compat_mp4", "subtitles", "hls"}  # thumbnails wait for a playable file
    assert {t.status for t in SubtitleTrack.objects.filter(media_file=file)} == {"pending"}

    # Subtitles: the embedded English track and the cp1256 Arabic sidecar, as UTF-8.
    run(file, TranscodeProfile.SUBTITLES)
    tracks = {t.language: t for t in SubtitleTrack.objects.filter(media_file=file)}
    arabic, english = tracks["ara"], tracks["eng"]
    assert (arabic.status, arabic.encoding, arabic.cues) == (SubtitleStatus.READY, "cp1256", 2)
    assert (english.status, english.storage_key, english.cues) == (SubtitleStatus.READY, "2.eng", 1)
    vtt = (asset(file) / "subs" / f"{arabic.storage_key}.vtt").read_text(encoding="utf-8")
    assert vtt.startswith("WEBVTT")
    assert "هذا مقطع اختبار مولَّد بواسطة FFmpeg." in vtt
    assert "شُكْرًا لِلمُشاهَدة!" in vtt
    assert "\r" not in vtt
    srt = (asset(file) / "subs" / f"{arabic.storage_key}.srt").read_text(encoding="utf-8")
    assert "شُكْرًا" in srt
    assert (asset(file) / "subs" / f"{arabic.storage_key}.m3u8").is_file()

    # The compat MP4 (a remux) carries both subtitles as mov_text.
    compat = run(file, TranscodeProfile.COMPAT_MP4)
    assert (compat.remux, compat.encoder) == (True, "copy")
    made = probe(asset(file) / "compat.mp4")
    assert sorted(s.language for s in made.subtitles) == ["ara", "eng"]
    assert {s.codec for s in made.subtitles} == {"mov_text"}
    assert Movie.objects.get(files=file).status == "ready"

    # Thumbnails follow: sprites, thumbs.vtt and, with no TMDB poster, one from a frame.
    run(file, TranscodeProfile.THUMBNAILS)
    thumbs = asset(file) / "thumbs"
    assert (thumbs / "sprite_001.jpg").is_file()
    cues = (thumbs / "thumbs.vtt").read_text()
    assert cues.startswith("WEBVTT")
    assert "sprite_001.jpg#xywh=0,0,160,90" in cues
    row = Rendition.objects.get(media_file=file, kind=RenditionKind.THUMBNAILS)
    assert (row.status, row.details["sheets"]) == (RenditionStatus.READY, 1)
    poster = MediaImage.objects.get(movie__files=file, kind=ImageKind.POSTER)
    assert (poster.source, poster.is_primary) == (ImageSource.UPLOAD, True)
    assert poster.width * 3 == pytest.approx(poster.height * 2, rel=0.02)  # a 2:3 crop

    # The ladder: 720p, 540p and 360p rungs, plus the 480p-capped presentation.
    hls = run(file, TranscodeProfile.HLS)
    assert hls.encoder == "libx264"
    for folder in ("v0", "v1", "v2", "a0"):
        verify_media_playlist(asset(file) / "hls" / folder, expected_duration_ms=2000)
    rungs = Rendition.objects.filter(media_file=file, kind=RenditionKind.HLS_VARIANT)
    assert sorted((r.name, r.height, r.details["box_height"]) for r in rungs) == [
        ("v0", 720, 720),
        ("v1", 540, 540),
        ("v2", 360, 360),
    ]
    masters = Rendition.objects.filter(media_file=file, kind=RenditionKind.HLS_MASTER)
    assert sorted((m.name, m.height) for m in masters) == [("hls", 720), ("hls480", 480)]
    master = parse_master_playlist((asset(file) / "hls" / "master.m3u8").read_text())
    assert master.variants[0].uri == "v1/index.m3u8"  # the 540p default first
    subs = sorted(m["LANGUAGE"] for m in master.media if m["TYPE"] == "SUBTITLES")
    assert subs == ["ar", "en"]
    audio = [m for m in master.media if m["TYPE"] == "AUDIO"]
    assert [(a["NAME"], a["DEFAULT"]) for a in audio] == [("English", "YES")]
    capped = parse_master_playlist((asset(file) / "hls480" / "master.m3u8").read_text())
    assert [v.uri for v in capped.variants] == ["v2/index.m3u8"]
    assert os.readlink(asset(file) / "hls480" / "v2") == "../hls/v2"
    assert not (asset(file) / "hls480" / "v0").exists()  # the token cannot reach 720p
    for scope in ("hls", "hls480", "compat"):
        assert os.readlink(asset(file) / scope / "subs") == "../subs"
        assert os.readlink(asset(file) / scope / "thumbs") == "../thumbs"

    # Playback offers the compat MP4 and both presentations, each with its ceiling.
    assert file.movie is not None
    title = playable_title(TitleKind.MOVIE, file.movie.xc_id)
    assert title is not None
    assert sorted((r.kind, r.token_rendition, r.height, r.entry) for r in title.renditions) == [
        (Kind.COMPAT, "compat", 720, "compat.mp4"),
        (Kind.HLS, "hls", 720, "hls/master.m3u8"),
        (Kind.HLS, "hls480", 480, "hls480/master.m3u8"),
    ]
    assert playable_title(TitleKind.MOVIE, file.movie.xc_id, uhd=True).renditions == ()  # type: ignore[union-attr]

    # Nothing is left over, and a second plan queues nothing.
    assert not any(layout.is_hidden(p.name) for p in asset(file).iterdir())
    assert services.prepare_file(file.pk) is Prepared.EXISTING
    assert not TranscodeJob.objects.filter(media_file=file, status=JobStatus.QUEUED).exists()


def test_an_admins_default_track_reaches_the_master(library: Library) -> None:
    file = film(library)
    services.prepare_file(file.pk)
    run(file, TranscodeProfile.SUBTITLES)
    run(file, TranscodeProfile.COMPAT_MP4)
    run(file, TranscodeProfile.HLS)
    SubtitleTrack.objects.filter(media_file=file, language="ara").update(default=True)
    AudioTrack.objects.filter(media_file=file).update(default=False)

    presentations.publish(file)

    master = parse_master_playlist((asset(file) / "hls" / "master.m3u8").read_text())
    defaults = {m["LANGUAGE"]: m["DEFAULT"] for m in master.media if m["TYPE"] == "SUBTITLES"}
    assert defaults == {"ar": "YES", "en": "NO"}
    # A group always has a default audio rendition.
    assert [m["DEFAULT"] for m in master.media if m["TYPE"] == "AUDIO"] == ["YES"]

    # Without the ladder, no presentation is left.
    Rendition.objects.filter(media_file=file, kind=RenditionKind.HLS_VARIANT).delete()
    assert presentations.publish(file) == []
    assert not (asset(file) / "hls480").exists()
    assert not Rendition.objects.filter(media_file=file, name="hls480").exists()


def test_a_uhd_source_is_kept_and_gets_its_rung_and_presentation(
    library: Library, media_keys: None
) -> None:
    file = uhd_source(library)

    services.prepare_file(file.pk)

    jobs = {j.profile: j for j in TranscodeJob.objects.filter(media_file=file)}
    assert set(jobs) == {"compat_mp4", "hls", "uhd"}
    assert (jobs["uhd"].backend, jobs["uhd"].remux, jobs["uhd"].priority) == ("cpu", True, 2)
    run(file, TranscodeProfile.UHD)
    link = asset(file) / "uhd.mkv"
    assert os.readlink(link) == str(Path(library.path) / "UHD.libx265.mkv")
    uhd = Rendition.objects.get(media_file=file, kind=RenditionKind.UHD)
    assert (uhd.height, uhd.codec, uhd.details["mode"], uhd.details["linked"]) == (
        1440,
        "hevc",
        "keep_source",
        True,
    )
    rung = Rendition.objects.get(media_file=file, kind=RenditionKind.HLS_VARIANT, name="uhd")
    assert rung.details["codecs"].startswith("hvc1.1.")
    assert (asset(file) / "hls2160" / "uhd" / "init.mp4").is_file()
    # No ladder yet: no UHD presentation.
    assert not (asset(file) / "hls2160" / "master.m3u8").exists()

    run(file, TranscodeProfile.COMPAT_MP4)
    run(file, TranscodeProfile.HLS)

    master = parse_master_playlist((asset(file) / "hls2160" / "master.m3u8").read_text())
    uris = [v.uri for v in master.variants]
    assert "uhd/index.m3u8" in uris
    assert "v0/index.m3u8" in uris
    assert os.readlink(asset(file) / "hls2160" / "v0") == "../hls/v0"
    sdr = parse_master_playlist((asset(file) / "hls" / "master.m3u8").read_text())
    assert "uhd/index.m3u8" not in [v.uri for v in sdr.variants]
    assert file.movie is not None
    offered = playable_title(TitleKind.MOVIE, file.movie.xc_id)
    assert offered is not None
    assert ("hls2160", 2160) in {(r.token_rendition, r.height) for r in offered.renditions}
    assert Kind.UHD not in {r.kind for r in offered.renditions}  # not instead of compat
    version = playable_title(TitleKind.MOVIE, file.movie.xc_id, uhd=True)
    assert version is not None
    assert [(r.kind, r.entry) for r in version.renditions] == [(Kind.UHD, "uhd.mkv")]


def test_a_uhd_encode_needs_an_hevc_encoder_or_the_cpu_setting(library: Library) -> None:
    file = uhd_source(library, codec="libx264")

    services.prepare_file(file.pk)

    assert not TranscodeJob.objects.filter(media_file=file, profile="uhd").exists()
    uhd = Rendition.objects.get(media_file=file, kind=RenditionKind.UHD)
    assert (uhd.status, uhd.error) == (RenditionStatus.FAILED, services.NO_HEVC_ENCODER)
    set_setting("library.uhd_cpu_encode", value=True, actor=None)
    assert services.reprocess(file, ["uhd"]) == {"uhd": "queued"}
    job = TranscodeJob.objects.get(media_file=file, profile="uhd")
    assert (job.backend, job.remux) == ("cpu", False)
    state_redis().set(
        "worker:caps:gpu",
        json.dumps({"host": "gpu", "backends": {"nvenc": ["h264", "hevc"]}}),
    )
    assert services.route_profile("uhd") == "nvenc"
    assert services.route_profile("uhd", remux=True) == "cpu"
    assert services.route_profile("hls") == "nvenc"
    assert services.route_profile("thumbnails") == "cpu"


def test_settings_switch_the_ladder_and_uhd_off(library: Library) -> None:
    set_setting("library.hls_enabled", value=False, actor=None)
    set_setting("library.uhd_enabled", value=False, actor=None)
    file = uhd_source(library)
    services.prepare_file(file.pk)
    assert set(TranscodeJob.objects.filter(media_file=file).values_list("profile", flat=True)) == {
        "compat_mp4"
    }


def test_reprocess_replaces_outputs_and_reports(library: Library, settings: Settings) -> None:
    file = film(library)
    services.prepare_file(file.pk)
    run(file, TranscodeProfile.SUBTITLES)
    run(file, TranscodeProfile.COMPAT_MP4)
    queued = services.reprocess(file, ["compat_mp4", "subtitles", "hls", "uhd"])
    assert queued == {
        "compat_mp4": "queued",
        "subtitles": "queued",
        "hls": "active",  # its first job has not run yet
        "uhd": "not_uhd",
    }
    # A ready output keeps serving while it is made again.
    compat = Rendition.objects.get(media_file=file, kind=RenditionKind.COMPAT_MP4)
    assert compat.status == RenditionStatus.READY
    assert {t.status for t in SubtitleTrack.objects.filter(media_file=file)} == {"pending"}
    run(file, TranscodeProfile.COMPAT_MP4)
    assert tasks.reprocess_media_file(str(file.pk), ["thumbnails"]) == {"thumbnails": "active"}
    (Path(library.path) / file.storage_key).unlink()
    assert services.reprocess(file, ["hls"]) == {"hls": "unreadable_source"}


def test_a_failed_subtitles_job_fails_its_pending_tracks(
    library: Library, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.TRANSCODE_MAX_ATTEMPTS = 1
    file = film(library)
    services.prepare_file(file.pk)
    job = TranscodeJob.objects.get(media_file=file, profile="subtitles")

    def broken(*_args: Any, **_kwargs: Any) -> None:
        from apps.media.ffmpeg import FfmpegError  # noqa: PLC0415

        raise FfmpegError(1, ["Invalid data found when processing input"])

    monkeypatch.setattr("apps.media.ffmpeg.run", broken)
    assert services.run_job(job.pk, job.dispatches) is JobStatus.FAILED
    assert set(SubtitleTrack.objects.filter(media_file=file).values_list("status", flat=True)) == {
        "failed"
    }
    services.retry_job(job, actor=None, ip=None)
    assert set(SubtitleTrack.objects.filter(media_file=file).values_list("status", flat=True)) == {
        "pending"
    }


def test_unreadable_sidecars_fail_alone(library: Library) -> None:
    file = film(library)
    sidecar = Path(library.path) / "Film (2020)" / "Film.2020.ar.srt"
    sidecar.write_bytes(b"\x00\x00\x00")
    services.prepare_file(file.pk)
    run(file, TranscodeProfile.SUBTITLES)
    tracks = {t.language: t for t in SubtitleTrack.objects.filter(media_file=file)}
    assert tracks["eng"].status == SubtitleStatus.READY
    assert tracks["ara"].status == SubtitleStatus.FAILED
    assert tracks["ara"].error in {"no_cues", "convert", "empty", "unknown_encoding"}
    # The compat MP4 leaves the unreadable sidecar out instead of failing.
    run(file, TranscodeProfile.COMPAT_MP4)
    assert [s.language for s in probe(asset(file) / "compat.mp4").subtitles] == ["eng"]
