# ruff: noqa: RUF001 (Arabic and Cyrillic subtitle text)
"""Subtitles (SPEC §7.3, ADR-0014): encoding detection (Windows-1256 Arabic without
breaking UTF-8), sidecar discovery and the track rows a probe and its sidecars give."""

import codecs
from collections.abc import Callable
from pathlib import Path

import pytest

from apps.catalog.models import FileState, MediaFile
from apps.library.models import Library
from apps.media import subtitles
from apps.media.models import (
    SUBTITLE_MAX_BYTES,
    AudioTrack,
    SubtitleFormat,
    SubtitleStatus,
    SubtitleTrack,
)
from apps.media.subtitles import SubtitleError, decode, find_sidecars, sync_tracks
from apps.media.tests.builders import audio, probe_result, subtitle, video

ARABIC = (
    "1\n00:00:01,000 --> 00:00:04,500\nهذا مقطع اختبار مولَّد بواسطة FFmpeg.\n\n"
    "2\n00:00:05,000 --> 00:00:09,000\nلا يحتوي على أي مشهد من فيلم حقيقي.\n\n"
    "3\n00:00:10,000 --> 00:00:14,000\nالترجمة مرمّزة بترميز Windows-1256،\nوتنتهي أسطرها بـ CRLF.\n"
)


# --- Encodings -------------------------------------------------------------------------------


def test_windows_1256_arabic_is_detected_with_and_without_a_hint() -> None:
    data = ARABIC.replace("\n", "\r\n").encode("cp1256")
    for hint in ("ara", None, "eng"):
        decoded = decode(data, hint)
        assert decoded.encoding == "cp1256", hint
        assert decoded.text == ARABIC  # CRLF folded, every letter intact


def test_utf8_is_kept_as_it_is_even_when_a_hint_says_otherwise() -> None:
    decoded = decode(ARABIC.encode("utf-8"), "ara")
    assert (decoded.text, decoded.encoding) == (ARABIC, "utf-8")


@pytest.mark.parametrize(
    ("data", "encoding"),
    [
        (codecs.BOM_UTF8 + ARABIC.encode("utf-8"), "utf-8"),
        (ARABIC.encode("utf-16"), "utf-16"),
        (codecs.BOM_UTF16_BE + ARABIC.encode("utf-16-be"), "utf-16"),
        (ARABIC.encode("utf-32"), "utf-32"),
    ],
)
def test_byte_order_marks(data: bytes, encoding: str) -> None:
    decoded = decode(data)
    assert decoded.encoding == encoding
    assert decoded.text == ARABIC


def test_western_and_cyrillic_code_pages() -> None:
    french = "1\n00:00:01,000 --> 00:00:02,000\nÇa été très réussi, déjà vu.\n"
    assert decode(french.encode("cp1252"), "fre").text == french
    russian = "1\n00:00:01,000 --> 00:00:02,000\nПривет, как дела? Всё хорошо.\n"
    decoded = decode(russian.encode("cp1251"), "rus")
    assert (decoded.text, decoded.encoding) == (russian, "cp1251")


def test_empty_and_counts() -> None:
    with pytest.raises(SubtitleError, match="empty"):
        decode(b"  \r\n")
    vtt = "WEBVTT\n\n00:01.000 --> 00:02.000\nA\n\n01:00:03.500 --> 01:00:04.000\nB\n"
    assert subtitles.count_cues(vtt) == 2
    assert subtitles.content_hash(b"abc") == subtitles.content_hash(b"abc")


# --- Sidecars --------------------------------------------------------------------------------


def touch(path: Path, data: bytes = b"1\n00:00:01,000 --> 00:00:02,000\nhi\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_sidecars_named_after_the_video(tmp_path: Path) -> None:
    folder = tmp_path / "Inception (2010)"
    video_file = touch(folder / "Inception.2010.1080p.mkv", b"")
    touch(folder / "Inception.2010.1080p.ar.srt")
    touch(folder / "Inception.2010.1080p.en.forced.ass")
    touch(folder / "Inception.2010.1080p.English.SDH.vtt")
    touch(folder / "Inception.2010.1080p_fre.default.ssa")
    touch(folder / "Other.Film.ar.srt")  # another video's
    touch(folder / "Inception.2010.1080p.nfo")  # not a subtitle

    found = {s.relative: s for s in find_sidecars(video_file, tmp_path)}

    assert sorted(found) == [
        "Inception (2010)/Inception.2010.1080p.English.SDH.vtt",
        "Inception (2010)/Inception.2010.1080p.ar.srt",
        "Inception (2010)/Inception.2010.1080p.en.forced.ass",
        "Inception (2010)/Inception.2010.1080p_fre.default.ssa",
    ]
    arabic = found["Inception (2010)/Inception.2010.1080p.ar.srt"]
    assert (arabic.language, arabic.format, arabic.forced) == ("ara", SubtitleFormat.SRT, False)
    forced = found["Inception (2010)/Inception.2010.1080p.en.forced.ass"]
    assert (forced.language, forced.format, forced.forced) == ("eng", SubtitleFormat.ASS, True)
    sdh = found["Inception (2010)/Inception.2010.1080p.English.SDH.vtt"]
    assert (sdh.language, sdh.hearing_impaired, sdh.format) == ("eng", True, SubtitleFormat.VTT)
    french = found["Inception (2010)/Inception.2010.1080p_fre.default.ssa"]
    assert (french.language, french.default) == ("fre", True)


def test_sidecars_in_a_subs_folder(tmp_path: Path) -> None:
    alone = tmp_path / "Film"
    video_file = touch(alone / "Film.2020.mkv", b"")
    touch(alone / "Subs" / "2_Arabic.srt")
    touch(alone / "Subs" / "3_English.srt")
    found = sorted((s.relative, s.language) for s in find_sidecars(video_file, tmp_path))
    assert found == [("Film/Subs/2_Arabic.srt", "ara"), ("Film/Subs/3_English.srt", "eng")]

    season = tmp_path / "Show" / "Season 01"
    first = touch(season / "Show.S01E01.mkv", b"")
    touch(season / "Show.S01E02.mkv", b"")
    touch(season / "subs" / "Show.S01E01.ar.srt")
    touch(season / "subs" / "Show.S01E02.ar.srt")
    touch(season / "subs" / "random.srt")  # several videos here: only named ones count
    assert [s.relative for s in find_sidecars(first, tmp_path)] == [
        "Show/Season 01/subs/Show.S01E01.ar.srt"
    ]


def test_unknown_language_and_missing_folder(tmp_path: Path) -> None:
    video_file = touch(tmp_path / "Clip.mkv", b"")
    touch(tmp_path / "Clip.sdh.srt")
    assert [(s.language, s.hearing_impaired) for s in find_sidecars(video_file, tmp_path)] == [
        ("und", True)
    ]
    assert find_sidecars(tmp_path / "gone" / "Clip.mkv", tmp_path) == []
    outside = touch(tmp_path.parent / f"{tmp_path.name}-x" / "Clip.mkv", b"")
    touch(outside.parent / "Clip.ar.srt")
    assert find_sidecars(outside, tmp_path) == []


def test_oversized_sidecars_are_refused(tmp_path: Path) -> None:
    big = tmp_path / "big.srt"
    big.write_bytes(b"x" * (SUBTITLE_MAX_BYTES + 1))
    with pytest.raises(SubtitleError, match="too_large"):
        subtitles.read_limited(big)


# --- Track rows ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_sync_tracks_keeps_edits_and_follows_the_files(
    make_library: Callable[..., Library], tmp_path: Path
) -> None:
    library = make_library()
    folder = Path(library.path)
    video_file = touch(folder / "Film.mkv", b"")
    sidecar = touch(folder / "Film.ar.srt", ARABIC.encode("cp1256"))
    file = MediaFile.objects.create(
        library=library, storage_key="Film.mkv", state=FileState.MATCHED
    )
    result = probe_result(
        video(),
        audio(1, tags={"language": "eng"}),
        audio(2, codec_name="ac3", channels=6, tags={"language": "ara", "title": "Arabic 5.1"},
              disposition={"default": 0, "forced": 0, "comment": 0}),
        subtitle(3, "subrip", tags={"language": "eng"}),
        subtitle(4, "hdmv_pgs_subtitle", tags={"language": "ara"}),
    )  # fmt: skip

    assert sync_tracks(file, result, find_sidecars(video_file, folder)) is True

    audio_rows = list(AudioTrack.objects.filter(media_file=file))
    assert [(a.stream_index, a.language, a.channels, a.default) for a in audio_rows] == [
        (1, "eng", 2, True),
        (2, "ara", 6, False),
    ]
    assert audio_rows[1].title == "Arabic 5.1"
    rows = {
        (t.stream_index, t.sidecar_path): t for t in SubtitleTrack.objects.filter(media_file=file)
    }
    assert rows[(3, "")].status == SubtitleStatus.PENDING
    assert rows[(3, "")].format == SubtitleFormat.SRT
    assert rows[(4, "")].status == SubtitleStatus.UNSUPPORTED
    assert not rows[(4, "")].is_text
    external = rows[(None, "Film.ar.srt")]
    assert (external.external, external.language, external.format) == (True, "ara", "srt")

    # An admin's edits survive a re-probe; converted tracks stay converted.
    AudioTrack.objects.filter(media_file=file, stream_index=2).update(default=True, title="AR")
    SubtitleTrack.objects.filter(pk=external.pk).update(
        status=SubtitleStatus.READY, source_hash=subtitles.content_hash(sidecar.read_bytes())
    )
    SubtitleTrack.objects.filter(media_file=file, stream_index=3).update(
        status=SubtitleStatus.READY
    )
    assert sync_tracks(file, result, find_sidecars(video_file, folder)) is False
    assert AudioTrack.objects.get(media_file=file, stream_index=2).title == "AR"

    # A changed sidecar is converted again; a gone stream or sidecar loses its row.
    sidecar.write_bytes(ARABIC.encode("utf-8"))
    fewer = probe_result(video(), audio(1), subtitle(3, "ass", tags={"language": "eng"}))
    assert sync_tracks(file, fewer, find_sidecars(video_file, folder)) is True
    assert AudioTrack.objects.filter(media_file=file).count() == 1
    assert SubtitleTrack.objects.get(pk=external.pk).status == SubtitleStatus.PENDING
    embedded = SubtitleTrack.objects.get(media_file=file, stream_index=3)
    assert (embedded.format, embedded.status) == (SubtitleFormat.ASS, SubtitleStatus.PENDING)
    assert not SubtitleTrack.objects.filter(stream_index=4).exists()
    sidecar.unlink()
    sync_tracks(file, fewer, find_sidecars(video_file, folder))
    assert not SubtitleTrack.objects.filter(pk=external.pk).exists()


@pytest.mark.django_db
def test_external_keys(make_library: Callable[..., Library]) -> None:
    library = make_library()
    file = MediaFile.objects.create(library=library, storage_key="a.mkv")
    assert subtitles.next_external_key(file, "ara") == "x1.ara"
    SubtitleTrack.objects.create(media_file=file, external=True, format="srt", storage_key="x1.ara")
    assert subtitles.next_external_key(file, "") == "x2.und"
    assert subtitles.subtitle_format("mov_text") == SubtitleFormat.SRT
    assert subtitles.subtitle_format("dvd_subtitle") == SubtitleFormat.VOBSUB
    assert subtitles.subtitle_format("eia_608") == SubtitleFormat.OTHER
