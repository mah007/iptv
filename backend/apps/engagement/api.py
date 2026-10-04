"""Customer API of playback and engagement (SPEC §3 web playback, §9, §10 Playback,
Engagement and `home`).

`playback/start` resolves the title the customer may browse, then runs the SPEC §7.4
checks through `playback.services.start_playback` on the request's device (the browser
session's `web` device, or the app's token device) and returns the signed edge URL,
where to resume, and the tracks and thumbnails when they exist. Progress is reported to
the API, never through the media edge.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import pagination
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.customer_auth import customer_of, device_for
from apps.catalog import home
from apps.catalog.api_customer import CatalogView
from apps.catalog.browse import playable_episodes, visible_movies, visible_series
from apps.catalog.home import row_title
from apps.catalog.models import Episode
from apps.catalog.playable import playable_title_by_id
from apps.catalog.serializers_customer import TitleCardSerializer
from apps.core.errors import ErrorCode, ProblemError
from apps.core.http import client_ip
from apps.core.schema import problems
from apps.engagement import services
from apps.engagement.recommend import recommender
from apps.engagement.serializers import (
    FavoriteAddedSerializer,
    HomeSerializer,
    PlaybackGrantSerializer,
    PlaybackStartSerializer,
    ProgressReportSerializer,
    ProgressSavedSerializer,
    RatingSerializer,
    RecommendationsSerializer,
    StopReportSerializer,
    TitleRefSerializer,
    WatchItemSerializer,
)
from apps.media.models import Rendition, RenditionKind, RenditionStatus
from apps.playback.concurrency import KickReason
from apps.playback.models import PlaybackSession, TitleKind
from apps.playback.services import (
    Delivery,
    PlaybackGrant,
    Prefer,
    start_playback,
    stop_sessions,
)

THUMBNAILS_VTT = "thumbs/thumbs.vtt"


# --- Playback ---------------------------------------------------------------------------------


def _episode_to_play(view: CatalogView, series_id: UUID, episode_id: UUID | None) -> Episode:
    user = customer_of(view.request)
    series = visible_series(view.scope()).filter(pk=series_id).first()
    if series is None:
        raise ProblemError(ErrorCode.NOT_FOUND, "No such title.")
    if episode_id is not None:
        episode = (
            playable_episodes()
            .filter(pk=episode_id, season__series=series)
            .select_related("season")
            .first()
        )
        if episode is None:
            # Known but not playable yet, or not part of this series.
            if Episode.objects.filter(pk=episode_id, season__series=series).exists():
                raise ProblemError(ErrorCode.TITLE_PREPARING)
            raise ProblemError(ErrorCode.NOT_FOUND, "No such episode.")
        return episode
    episode = services.next_episode(user, series)
    if episode is None:
        raise ProblemError(ErrorCode.TITLE_PREPARING)
    return episode


def _asset_file_id(grant: PlaybackGrant) -> UUID | None:
    return (
        Rendition.objects.filter(storage_key=grant.rendition.storage_key)
        .values_list("media_file_id", flat=True)
        .first()
    )


def _tracks(grant: PlaybackGrant) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Audio and subtitle tracks of the playing file, and its thumbnails, when known.

    Subtitles and thumbnails live in the asset next to the stream and are reachable
    with the same token under the rendition's scope (`<scope>/subs/`, `<scope>/thumbs/`).
    """
    from apps.media.models import AudioTrack, SubtitleStatus, SubtitleTrack  # noqa: PLC0415

    file_id = _asset_file_id(grant)
    if file_id is None:
        return [], [], None
    base = grant.url[: -len(grant.rendition.entry)] + grant.rendition.token_rendition
    audio = [
        {
            "language": row.language or "und",
            "codec": row.codec,
            "channels": row.channels,
            "default": row.default,
        }
        for row in AudioTrack.objects.filter(media_file_id=file_id, commentary=False)
    ]
    subtitles = [
        {
            "language": row.language or "und",
            "name": row.title,
            "forced": row.forced,
            "url": f"{base}/subs/{row.storage_key}.vtt",
        }
        for row in SubtitleTrack.objects.filter(
            media_file_id=file_id, status=SubtitleStatus.READY
        ).exclude(storage_key="")
    ]
    has_thumbs = Rendition.objects.filter(
        media_file_id=file_id, kind=RenditionKind.THUMBNAILS, status=RenditionStatus.READY
    ).exists()
    return audio, subtitles, f"{base}/{THUMBNAILS_VTT}" if has_thumbs else None


class PlaybackStartView(CatalogView):
    @extend_schema(
        operation_id="playback_start",
        summary="Start playing a movie or an episode in this browser or app",
        description="Runs the entitlement checks (SUBSCRIPTION_EXPIRED, DEVICE_BLOCKED, "
        "CATEGORY_NOT_ALLOWED, QUALITY_NOT_ALLOWED, CONCURRENCY_LIMIT, ...) and returns "
        "the signed stream URL. TITLE_PREPARING (409) while the title is being prepared.",
        request=PlaybackStartSerializer,
        responses={200: PlaybackGrantSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request) -> Response:
        payload = PlaybackStartSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        user = customer_of(request)
        episode: Episode | None = None
        if data["title_type"] == "movie":
            if not visible_movies(self.scope()).filter(pk=data["title_id"]).exists():
                raise ProblemError(ErrorCode.NOT_FOUND, "No such title.")
            kind, title_id = TitleKind.MOVIE, data["title_id"]
        else:
            episode = _episode_to_play(self, data["title_id"], data.get("episode_id"))
            kind, title_id = TitleKind.EPISODE, episode.pk
        playable = playable_title_by_id(kind, title_id)
        if playable is None:
            raise ProblemError(ErrorCode.TITLE_PREPARING)
        prefer = Prefer(data["prefer"]) if data.get("prefer") else None
        grant = start_playback(
            user,
            device_for(request, user),
            playable,
            prefer=prefer,
            client_ip=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        audio, subtitles, thumbnails = _tracks(grant)
        segmented = grant.rendition.delivery is Delivery.SEGMENTED
        body = {
            "session_id": grant.session.pk,
            "title_type": kind.value,
            "title_id": title_id,
            "episode": (
                {"id": episode.pk, "season_number": episode.season.number, "number": episode.number}
                if episode is not None
                else None
            ),
            "delivery": grant.rendition.delivery.value,
            "url": grant.url,
            "hls_master_url": grant.url if segmented else None,
            "compat_url": None if segmented else grant.url,
            "height": grant.rendition.height,
            "expires_at": datetime.fromtimestamp(grant.expires_at, tz=UTC),
            "duration_ms": max(0, playable.runtime_s) * 1000,
            "resume_ms": services.resume_position(user, kind, title_id),
            "audio": audio,
            "subtitles": subtitles,
            "thumbnails_url": thumbnails,
        }
        response = Response(PlaybackGrantSerializer(body).data)
        response["Cache-Control"] = "no-store"
        return response


def _session_of(request: Request, pk: UUID) -> PlaybackSession:
    session = PlaybackSession.objects.filter(pk=pk, user=customer_of(request)).first()
    if session is None:
        raise ProblemError(ErrorCode.NOT_FOUND, "No such playback session.")
    return session


class PlaybackProgressView(CatalogView):
    @extend_schema(
        operation_id="playback_progress",
        summary="Report the position (every 15 s and on pause)",
        description="Reports closer together than playback.progress_min_interval_s are not "
        "saved (saved=false).",
        request=ProgressReportSerializer,
        responses={200: ProgressSavedSerializer, **problems(400, 401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        payload = ProgressReportSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        result = services.record_progress(
            customer_of(request),
            _session_of(request, pk),
            payload.validated_data["position_ms"],
            payload.validated_data.get("duration_ms"),
        )
        row = result.progress
        return Response(
            {
                "saved": result.saved,
                "position_ms": row.position_ms if row else None,
                "completed": row.completed if row else None,
            }
        )


class PlaybackStopView(CatalogView):
    @extend_schema(
        operation_id="playback_stop",
        summary="Stop the stream (on exit); saves the final position when given",
        request=StopReportSerializer,
        responses={204: OpenApiResponse(description="Stopped."), **problems(400, 401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        payload = StopReportSerializer(data=request.data or {})
        payload.is_valid(raise_exception=True)
        user = customer_of(request)
        session = _session_of(request, pk)
        position = payload.validated_data.get("position_ms")
        if position is not None:
            services.record_progress(
                user, session, position, payload.validated_data.get("duration_ms"), force=True
            )
        if session.ended_at is None:
            # Ends as "stopped": the edge refuses the token's next request.
            stop_sessions([session], KickReason.REPLACED)
        return Response(status=204)


# --- Continue watching, history, favourites, ratings ------------------------------------------


class ContinueWatchingView(CatalogView):
    @extend_schema(
        operation_id="continue_watching_list",
        summary="Titles to resume, newest first (one entry per series)",
        responses={200: WatchItemSerializer(many=True), **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        items = services.continue_watching(customer_of(request), self.scope())
        return Response(WatchItemSerializer(items, many=True, context=self.context()).data)


class HistoryPagination(pagination.CursorPagination):
    page_size = 30
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = ("-updated_at", "-id")

    def get_paginated_response_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["next", "previous", "results"],
            "properties": {
                "next": {"type": ["string", "null"], "format": "uri"},
                "previous": {"type": ["string", "null"], "format": "uri"},
                "results": schema,
            },
        }


class WatchHistoryView(CatalogView):
    pagination_class = HistoryPagination

    @extend_schema(
        operation_id="watch_history_list",
        summary="Everything you watched, newest first",
        parameters=[
            OpenApiParameter("cursor", str, description="The `next` or `previous` cursor."),
            OpenApiParameter("page_size", int, description="Items per page (30, at most 100)."),
        ],
        responses={200: WatchItemSerializer(many=True), **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        user = customer_of(request)
        paginator = HistoryPagination()
        rows = paginator.paginate_queryset(services.history(user, self.scope()), request, self)
        items = services.history_items(self.scope(), rows or [])
        data = WatchItemSerializer(items, many=True, context=self.context()).data
        return paginator.get_paginated_response(data)


class WatchHistoryDetailView(CatalogView):
    @extend_schema(
        operation_id="watch_history_delete",
        summary="Forget one title (it also leaves continue-watching)",
        responses={204: None, **problems(401, 403, 404)},
    )
    def delete(self, request: Request, pk: UUID) -> Response:
        services.forget(customer_of(request), pk)
        return Response(status=204)


class FavoriteListView(CatalogView):
    @extend_schema(
        operation_id="favorites_list",
        summary="My list, newest first",
        responses={200: TitleCardSerializer(many=True), **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        titles = services.favorites(customer_of(request), self.scope())
        return Response(TitleCardSerializer(titles, many=True, context=self.context()).data)

    @extend_schema(
        operation_id="favorites_add",
        summary="Add a title to my list",
        request=TitleRefSerializer,
        responses={
            201: FavoriteAddedSerializer,
            200: FavoriteAddedSerializer,
            **problems(400, 401, 403, 404),
        },
    )
    def post(self, request: Request) -> Response:
        payload = TitleRefSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        added = services.add_favorite(
            customer_of(request), self.scope(), data["title_type"], data["title_id"]
        )
        body = {"title_type": data["title_type"], "title_id": data["title_id"], "added": added}
        return Response(FavoriteAddedSerializer(body).data, status=201 if added else 200)


class FavoriteDetailView(CatalogView):
    @extend_schema(
        operation_id="favorites_delete",
        summary="Remove a title from my list",
        responses={204: None, **problems(401, 403)},
    )
    def delete(self, request: Request, title_type: str, pk: UUID) -> Response:
        if title_type not in {"movie", "series"}:
            raise ProblemError(ErrorCode.NOT_FOUND)
        services.remove_favorite(customer_of(request), title_type, pk)
        return Response(status=204)


class RatingView(CatalogView):
    @extend_schema(
        operation_id="ratings_set",
        summary="Thumbs up or down on a title (null clears it)",
        request=RatingSerializer,
        responses={200: RatingSerializer, **problems(400, 401, 403, 404)},
    )
    def post(self, request: Request) -> Response:
        payload = RatingSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        value = services.set_rating(
            customer_of(request), self.scope(), data["title_type"], data["title_id"], data["value"]
        )
        body = {"title_type": data["title_type"], "title_id": data["title_id"], "value": value}
        return Response(RatingSerializer(body).data)


# --- Recommendations and home -----------------------------------------------------------------


class RecommendationsView(CatalogView):
    @extend_schema(
        operation_id="recommendations_retrieve",
        summary="Because you watched, top picks and popular this week",
        responses={200: RecommendationsSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        user, scope, engine = customer_of(request), self.scope(), recommender()
        body = {
            "because_you_watched": engine.because_you_watched(user, scope),
            "top_picks": engine.top_picks(user, scope),
            "popular": engine.popular(scope),
        }
        return Response(RecommendationsSerializer(body, context=self.context()).data)


class HomeView(CatalogView):
    @extend_schema(
        operation_id="home_retrieve",
        summary="The home screen: hero, continue watching and rows",
        description="Rows in order: recently added, popular this week, because you "
        "watched (one per recent title), top picks, collections, then one per category.",
        responses={200: HomeSerializer, **problems(401, 403)},
    )
    def get(self, request: Request) -> Response:
        user, scope, locale = customer_of(request), self.scope(), self.locale()
        context = self.context()
        shared = home.shared_rows(scope, locale)
        engine = recommender()
        personal: list[dict[str, Any]] = []
        for row in engine.because_you_watched(user, scope)[:2]:
            source = dict(TitleCardSerializer(row.source, context=context).data)
            personal.append(
                {
                    "kind": "because_you_watched",
                    "key": f"because:{source['type']}:{source['id']}",
                    "title": row_title("because_you_watched", locale, title=source["title"]),
                    "because_of": source,
                    "items": TitleCardSerializer(row.items, many=True, context=context).data,
                }
            )
        picks = engine.top_picks(user, scope)
        if picks:
            personal.append(
                {
                    "kind": "top_picks",
                    "key": "top_picks",
                    "title": row_title("top_picks", locale),
                    "items": TitleCardSerializer(picks, many=True, context=context).data,
                }
            )
        rows = shared["rows"]
        leading = [row for row in rows if row["kind"] in {"recently_added", "popular"}]
        trailing = [row for row in rows if row["kind"] not in {"recently_added", "popular"}]
        body = {
            "hero": shared["hero"],
            "continue_watching": WatchItemSerializer(
                services.continue_watching(user, scope), many=True, context=context
            ).data,
            "rows": [*leading, *personal, *trailing],
        }
        return Response(body)
