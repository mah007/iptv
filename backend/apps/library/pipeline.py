"""Per-file ingest (SPEC §7.2 steps 1-3): parse and probe a media file, then hand it to
metadata matching, which classifies it (library kind plus parse; `ambiguous` parses go
to review).

The probe summary goes into columns and `probe_summary`, the raw ffprobe JSON (without
the file name) into `probe`, and `direct_play` records whether the source plays as is
in every client (P3 `planner.is_direct_playable`), which is slice 2's "playable".
"""

import logging
from collections.abc import Callable
from uuid import UUID

from django.db import transaction

from apps.catalog.models import FileState, MediaFile
from apps.catalog.services import refresh_status_of_files
from apps.library import storage
from apps.library.parsing import parse_path
from apps.media.planner import is_direct_playable
from apps.media.probe import ProbeError, ProbeResult, probe
from apps.media.profiles import ProfileError, default_profiles

logger = logging.getLogger(__name__)

ERROR_MAX = 500


def apply_probe(file: MediaFile, result: ProbeResult) -> None:
    """Copy a probe into the file's columns (no save)."""
    summary = result.summary()
    file.container = summary["container"] or ""
    file.duration_ms = summary["duration_ms"]
    file.bitrate = summary["bitrate"]
    file.video_codec = summary["video_codec"] or ""
    file.video_profile = summary["video_profile"] or ""
    file.video_level = summary["video_level"]
    file.width = summary["width"]
    file.height = summary["height"]
    file.fps = summary["fps"]
    file.hdr = summary["hdr"] or ""
    file.probe = dict(result.raw)
    file.probe_summary = summary
    try:
        file.direct_play = is_direct_playable(result, default_profiles())
    except ProfileError:
        logger.exception("transcoding profiles unreadable; treating the file as not playable")
        file.direct_play = False


def process_file(
    file_id: UUID | str, *, match: Callable[[UUID], None] | None = None
) -> MediaFile | None:
    """Probe, parse and classify one file; queue its metadata match (`match(file_id)`,
    default the Celery task) unless it is already matched. Returns the file, or None
    when it no longer exists or was removed."""
    file = MediaFile.objects.select_related("library").filter(pk=file_id).first()
    if file is None or file.removed_at is not None:
        return None
    library = file.library
    parsed = parse_path(file.storage_key, library_kind=library.kind)  # type: ignore[arg-type]
    file.parse_result = parsed.to_json()
    try:
        result = probe(storage.library_file(library.path, file.storage_key))
    except (ProbeError, ValueError) as exc:
        file.state = FileState.ERROR
        file.error = str(exc)[:ERROR_MAX]
        file.save()
        refresh_status_of_files([file.pk])
        return file
    apply_probe(file, result)
    file.error = ""
    linked = file.movie_id is not None or file.episodes.exists()
    file.state = FileState.MATCHED if linked else FileState.MATCHING
    with transaction.atomic():
        file.save()
        if linked:
            refresh_status_of_files([file.pk])
        else:
            if match is None:
                match = _enqueue_match
            match(file.pk)
    return file


def _enqueue_match(file_id: UUID) -> None:
    from apps.metadata import tasks  # noqa: PLC0415 (metadata tasks import the pipeline)

    transaction.on_commit(lambda: tasks.match_media_file.delay(str(file_id)))
