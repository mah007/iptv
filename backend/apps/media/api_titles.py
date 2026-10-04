"""Admin API of a title's media (SPEC §8.3 Title detail, §10 `admin/titles/{id}/...`).

`{id}` is a movie's, a series' or an episode's id.

- `GET titles/{id}/renditions`: each file with its renditions (status, size, disk use),
  tracks and running jobs; `DELETE titles/{id}/renditions/{rendition}`.
- `POST titles/{id}/reprocess`: make outputs again (202; the worker queues the jobs).
- `GET titles/{id}/tracks` (the same document), `POST` a subtitle upload (multipart),
  `PATCH titles/{id}/tracks/{track}` (language, title, default, forced) and `DELETE`
  an uploaded subtitle.
- `GET titles/{id}/images` (stored artwork and TMDB alternatives), `POST` one (a TMDB
  path or an upload, 202), `POST titles/{id}/images/{image}/primary`, `DELETE`.
- `POST titles/{id}/rematch`: link the files to another TMDB title, or match again.
- `GET renditions/cleanup` (the latest report), `POST` a run (dry run by default, 202).

RBAC: `library.view` (or `library.manage`) reads, `library.manage` acts, a rematch also
accepts `library.review`. Every change is audited by `apps.media.titles`.
"""

import json
from typing import Any, ClassVar, cast
from uuid import UUID

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasPermission, Requirements
from apps.audit import services as audit
from apps.core.errors import ErrorCode, ProblemError
from apps.core.http import acting_user, client_ip
from apps.core.schema import problems
from apps.core.stores import cache_redis
from apps.media import tasks, titles
from apps.media.models import SUBTITLE_MAX_BYTES, AudioTrack
from apps.media.serializers import (
    CleanupReportSerializer,
    CleanupRequestSerializer,
    ImageAddSerializer,
    MediaQueuedSerializer,
    MediaSubtitleTrackSerializer,
    RematchResultSerializer,
    RematchSerializer,
    ReprocessResultSerializer,
    ReprocessSerializer,
    SubtitleUploadSerializer,
    TitleImagesSerializer,
    TitleMediaSerializer,
    TrackSerializer,
    TrackUpdateSerializer,
)

VIEW: tuple[str, ...] = ("library.view", "library.manage")
MANAGE = "library.manage"


class _TitleView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {
        "GET": VIEW,
        "POST": MANAGE,
        "PATCH": MANAGE,
        "DELETE": MANAGE,
    }


def _media(title: titles.Title) -> dict[str, Any]:
    document = {
        "title": {"kind": title.kind, "id": title.id, "name": title.name},
        "files": titles.files_with_media(title),
    }
    return dict(TitleMediaSerializer(document).data)


class TitleRenditionsView(_TitleView):
    @extend_schema(
        operation_id="titles_renditions_list",
        summary="A title's files with their renditions, tracks and running jobs",
        responses={200: TitleMediaSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(_media(titles.resolve_title(pk)))


class TitleRenditionDetailView(_TitleView):
    @extend_schema(
        operation_id="titles_renditions_delete",
        summary="Delete a rendition",
        description=(
            "Players stop getting it at once; the next cleanup deletes its files. "
            "Deleting the ladder (`hls`) deletes its rungs and capped presentations, "
            "deleting `uhd` its HLS rung. 409 for the source, a rung or while a job "
            "of the file runs."
        ),
        request=None,
        responses={204: None, **problems(401, 403, 404, 409)},
    )
    def delete(self, request: Request, pk: UUID, rendition: UUID) -> Response:
        title = titles.resolve_title(pk)
        titles.delete_rendition(title, rendition, actor=acting_user(request), ip=client_ip(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


class TitleReprocessView(_TitleView):
    @extend_schema(
        operation_id="titles_reprocess",
        summary="Make a title's outputs again",
        description=(
            "Queued on the worker: each file is probed again and one job per output "
            "is queued (an output whose job is running is left alone). A ready output "
            "keeps playing until the new one replaces it."
        ),
        request=ReprocessSerializer,
        responses={202: ReprocessResultSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        title = titles.resolve_title(pk)
        body = ReprocessSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        outputs = list(body.validated_data.get("outputs") or [])
        files = titles.reprocess(
            title,
            outputs,
            file_id=body.validated_data.get("media_file"),
            actor=acting_user(request),
            ip=client_ip(request),
        )
        chosen = outputs or list(titles.REPROCESSABLE)
        payload = {"files": files, "outputs": chosen}
        return Response(ReprocessResultSerializer(payload).data, status=status.HTTP_202_ACCEPTED)


class TitleTracksView(_TitleView):
    parser_classes = (MultiPartParser, JSONParser)

    @extend_schema(
        operation_id="titles_tracks_list",
        summary="A title's files with their audio and subtitle tracks",
        responses={200: TitleMediaSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(_media(titles.resolve_title(pk)))

    @extend_schema(
        operation_id="titles_tracks_create",
        summary="Upload a subtitle",
        description=(
            "An .srt, .ass, .ssa or .vtt file in any encoding (Windows-1256 Arabic "
            "included); it is converted to UTF-8 WebVTT and SRT and added to the HLS "
            "masters, and to the compat MP4 at its next reprocess."
        ),
        request={"multipart/form-data": SubtitleUploadSerializer},
        responses={201: MediaSubtitleTrackSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        title = titles.resolve_title(pk)
        body = SubtitleUploadSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        upload = body.validated_data["file"]
        if upload.size > SUBTITLE_MAX_BYTES:
            raise ProblemError(
                ErrorCode.VALIDATION_ERROR,
                "The subtitle file is too large (4 MB at most).",
                status=400,
                field_errors={"file": ["Too large."]},
            )
        track = titles.add_subtitle(
            title,
            name=upload.name or "subtitle.srt",
            data=upload.read(),
            language=body.validated_data["language"],
            track_title=body.validated_data.get("title") or "",
            default=body.validated_data["default"],
            forced=body.validated_data["forced"],
            file_id=body.validated_data.get("media_file"),
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(MediaSubtitleTrackSerializer(track).data, status=status.HTTP_201_CREATED)


class TitleTrackDetailView(_TitleView):
    @extend_schema(
        operation_id="titles_tracks_update",
        summary="Edit a track's language, title, default or forced flag",
        description=(
            "One default per kind and file: making a track default clears the others. "
            "The HLS masters follow at once; the compat MP4 at its next reprocess."
        ),
        request=TrackUpdateSerializer,
        responses={200: TrackSerializer, **problems(400, 401, 403, 404)},
    )
    def patch(self, request: Request, pk: UUID, track: UUID) -> Response:
        title = titles.resolve_title(pk)
        body = TrackUpdateSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        updated = titles.update_track(
            title,
            track,
            dict(body.validated_data),
            actor=acting_user(request),
            ip=client_ip(request),
        )
        if isinstance(updated, AudioTrack):
            data = {"kind": "audio", "audio": updated, "subtitle": None}
        else:
            data = {"kind": "subtitle", "audio": None, "subtitle": updated}
        return Response(TrackSerializer(data).data)

    @extend_schema(
        operation_id="titles_tracks_delete",
        summary="Delete an uploaded subtitle",
        description="409 for embedded and sidecar subtitles: they follow the library files.",
        request=None,
        responses={204: None, **problems(401, 403, 404, 409)},
    )
    def delete(self, request: Request, pk: UUID, track: UUID) -> Response:
        title = titles.resolve_title(pk)
        titles.delete_subtitle(title, track, actor=acting_user(request), ip=client_ip(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


class TitleImagesView(_TitleView):
    parser_classes = (MultiPartParser, JSONParser)

    @extend_schema(
        operation_id="titles_images_list",
        summary="A title's artwork and TMDB's alternatives",
        responses={200: TitleImagesSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        title = titles.resolve_title(pk)
        alternatives, error = titles.image_alternatives(title)
        document = {
            "title": {"kind": title.kind, "id": title.id, "name": title.name},
            "images": titles.title_images(title),
            "alternatives": alternatives,
            "alternatives_error": error,
        }
        return Response(TitleImagesSerializer(document).data)

    @extend_schema(
        operation_id="titles_images_create",
        summary="Add artwork: a TMDB alternative or an upload",
        description=(
            "Send `tmdb_path` (from the alternatives) as JSON, or `file` as multipart. "
            "The worker converts it (WebP and AVIF, every size); the image appears in "
            "the list a few seconds later."
        ),
        request={
            "application/json": ImageAddSerializer,
            "multipart/form-data": ImageAddSerializer,
        },
        responses={202: MediaQueuedSerializer, **problems(400, 401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        title = titles.resolve_title(pk)
        body = ImageAddSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        upload = body.validated_data.get("file")
        titles.add_image(
            title,
            kind=body.validated_data["kind"],
            tmdb_path=body.validated_data.get("tmdb_path"),
            upload=upload.read() if upload is not None else None,
            primary=body.validated_data["primary"],
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response({"queued": True}, status=status.HTTP_202_ACCEPTED)


class TitleImagePrimaryView(_TitleView):
    @extend_schema(
        operation_id="titles_images_primary",
        summary="Make an image the primary one of its kind",
        request=None,
        responses={200: TitleImagesSerializer, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID, image: UUID) -> Response:
        title = titles.resolve_title(pk)
        titles.set_primary_image(title, image, actor=acting_user(request), ip=client_ip(request))
        document = {
            "title": {"kind": title.kind, "id": title.id, "name": title.name},
            "images": titles.title_images(title),
            "alternatives": [],
            "alternatives_error": None,
        }
        return Response(TitleImagesSerializer(document).data)


class TitleImageDetailView(_TitleView):
    @extend_schema(
        operation_id="titles_images_delete",
        summary="Delete an image",
        description="The newest image of the same kind becomes primary if this one was.",
        request=None,
        responses={204: None, **problems(401, 403, 404)},
    )
    def delete(self, request: Request, pk: UUID, image: UUID) -> Response:
        title = titles.resolve_title(pk)
        titles.delete_image(title, image, actor=acting_user(request), ip=client_ip(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


class TitleRematchView(_TitleView):
    required_permissions: ClassVar[Requirements] = {"POST": ("library.review", MANAGE)}

    @extend_schema(
        operation_id="titles_rematch",
        summary="Link the title's files to another TMDB title, or match them again",
        description=(
            "With `tmdb_id`: every file is linked to that TMDB movie or series (episodes "
            "by their season and episode numbers). Without it: the files are matched "
            "again automatically and may land in the review queue."
        ),
        request=RematchSerializer,
        responses={200: RematchResultSerializer, **problems(400, 401, 403, 404, 409, 503)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        title = titles.resolve_title(pk)
        body = RematchSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        result = titles.rematch(
            title,
            tmdb_id=body.validated_data.get("tmdb_id"),
            kind=body.validated_data.get("kind"),
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(RematchResultSerializer(result).data)


class RenditionCleanupView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "POST": MANAGE}

    @extend_schema(
        operation_id="renditions_cleanup_report",
        summary="The latest rendition cleanup report",
        description="404 until a cleanup (or a dry run) has run.",
        responses={200: CleanupReportSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request) -> Response:
        raw = cache_redis().get(tasks.CLEANUP_REPORT_KEY)
        if raw is None:
            raise ProblemError(ErrorCode.NOT_FOUND, "No cleanup has run yet.", status=404)
        return Response(CleanupReportSerializer(json.loads(cast(bytes, raw))).data)

    @extend_schema(
        operation_id="renditions_cleanup_run",
        summary="Run the rendition cleanup (a dry run unless dry_run is false)",
        description=(
            "Orphaned asset folders, folders of files removed longer than the retention, "
            "superseded outputs and leftovers of interrupted jobs. Runs on the worker; "
            "read the report with GET."
        ),
        request=CleanupRequestSerializer,
        responses={
            202: OpenApiResponse(MediaQueuedSerializer, description="Queued."),
            **problems(400, 401, 403),
        },
    )
    def post(self, request: Request) -> Response:
        body = CleanupRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        dry_run = body.validated_data["dry_run"]
        audit.record(
            "renditions.cleanup",
            actor=acting_user(request),
            after={"dry_run": dry_run},
            ip=client_ip(request),
        )
        tasks.cleanup_renditions.delay(dry_run=dry_run)
        return Response({"queued": True}, status=status.HTTP_202_ACCEPTED)
