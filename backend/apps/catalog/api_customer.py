"""Customer API of the catalogue (SPEC §9 Browse and Title page, §10 Catalog).

Every view shows only what the customer may browse (`browse`: entitlement categories
and content kinds, adult opt-in, ready titles), in the request's language (`i18n`).
Large lists use cursor pagination (`?cursor=`), ordered by `sort`.
"""

from typing import Any
from uuid import UUID

from django.db.models import Exists, OuterRef, Q, QuerySet, Value
from django.db.models.functions import Coalesce
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import pagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.customer_auth import CUSTOMER_AUTHENTICATION, IsCustomer, customer_of
from apps.catalog import queries
from apps.catalog.browse import (
    CustomerScope,
    playable_episodes,
    scope_for,
    visible_categories,
    visible_movies,
    visible_series,
)
from apps.catalog.i18n import Locale, mark_language, request_locale
from apps.catalog.models import (
    Collection,
    CollectionItem,
    Credit,
    Genre,
    MediaFile,
    Movie,
    Person,
    Season,
    Series,
)
from apps.catalog.serializers_customer import (
    CategorySerializer,
    CollectionSerializer,
    EpisodeDetailSerializer,
    GenreSerializer,
    MovieDetailSerializer,
    PersonDetailSerializer,
    SeasonDetailSerializer,
    SeriesDetailSerializer,
    TitleCardSerializer,
)
from apps.catalog.services import playable_files
from apps.core.errors import ErrorCode, ProblemError
from apps.core.schema import problems
from apps.media.models import Rendition, RenditionStatus

SORTS: dict[str, tuple[str, ...]] = {
    "added": ("-created_at", "-id"),
    "popular": ("-popularity", "-id"),
    "rating": ("-sort_rating", "-id"),
    "year": ("-sort_year", "-id"),
    "title": ("title", "id"),
}


class TitleCursorPagination(pagination.CursorPagination):
    """`?cursor=` pages of `page_size` (24, up to 60), in the `sort` order."""

    page_size = 24
    page_size_query_param = "page_size"
    max_page_size = 60
    ordering = SORTS["added"]

    def get_ordering(self, request: Any, queryset: Any, view: Any) -> tuple[str, ...]:
        return SORTS.get(request.query_params.get("sort", "added"), SORTS["added"])

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


class CatalogView(APIView):
    """A customer endpoint answering in the request's language."""

    authentication_classes = CUSTOMER_AUTHENTICATION
    permission_classes = (IsCustomer,)

    def scope(self) -> CustomerScope:
        cached: CustomerScope | None = getattr(self, "_scope", None)
        if cached is None:
            cached = scope_for(customer_of(self.request))
            self._scope = cached
        return cached

    def locale(self) -> Locale:
        return request_locale(self.request)

    def context(self, **extra: Any) -> dict[str, Any]:
        return {"locale": self.locale(), "request": self.request, **extra}

    def finalize_response(self, request: Request, response: Any, *args: Any, **kwargs: Any) -> Any:
        response = super().finalize_response(request, response, *args, **kwargs)
        return mark_language(response, request_locale(request))


def not_found(what: str = "title") -> ProblemError:
    return ProblemError(ErrorCode.NOT_FOUND, f"No such {what}.")


# --- Lists ------------------------------------------------------------------------------------

_LIST_PARAMS = [
    OpenApiParameter("category", UUID, description="Only titles in this category."),
    OpenApiParameter("genre", UUID, description="Only titles of this genre."),
    OpenApiParameter("year", int, description="Only titles from this year."),
    OpenApiParameter(
        "sort",
        str,
        enum=list(SORTS),
        description="added (default), popular, rating, year or title.",
    ),
    OpenApiParameter("cursor", str, description="The `next` or `previous` link's cursor."),
    OpenApiParameter("page_size", int, description="Items per page (24, at most 60)."),
]


class _TitleListView(CatalogView):
    def titles(self) -> QuerySet[Any]:
        raise NotImplementedError

    def get(self, request: Request) -> Response:
        query = self.titles()
        params = request.query_params
        try:
            if params.get("category"):
                category = UUID(params["category"])
                if not visible_categories(self.scope()).filter(pk=category).exists():
                    raise not_found("category")
                query = query.filter(categories=category)
            if params.get("genre"):
                query = query.filter(genres=UUID(params["genre"]))
            if params.get("year"):
                query = query.filter(year=int(params["year"]))
        except ValueError:
            raise ProblemError(ErrorCode.VALIDATION_ERROR, "Invalid filter value.") from None
        query = queries.cards(
            query.distinct().annotate(
                sort_rating=Coalesce("rating", Value(-1.0)), sort_year=Coalesce("year", Value(0))
            )
        )
        paginator = TitleCursorPagination()
        page = paginator.paginate_queryset(query, request, view=self)
        data = TitleCardSerializer(page, many=True, context=self.context()).data
        return paginator.get_paginated_response(data)


class MovieListView(_TitleListView):
    pagination_class = TitleCursorPagination

    def titles(self) -> QuerySet[Movie]:
        return visible_movies(self.scope())

    @extend_schema(
        operation_id="movies_list",
        summary="Movies you can watch",
        parameters=_LIST_PARAMS,
        responses={200: TitleCardSerializer(many=True), **problems(400, 401, 403, 404)},
    )
    def get(self, request: Request) -> Response:
        return super().get(request)


class SeriesListView(_TitleListView):
    pagination_class = TitleCursorPagination

    def titles(self) -> QuerySet[Series]:
        return visible_series(self.scope())

    @extend_schema(
        operation_id="series_list",
        summary="Series you can watch",
        parameters=_LIST_PARAMS,
        responses={200: TitleCardSerializer(many=True), **problems(400, 401, 403, 404)},
    )
    def get(self, request: Request) -> Response:
        return super().get(request)


# --- Title pages ------------------------------------------------------------------------------


def _best_height(files: QuerySet[MediaFile], ceiling: int) -> int:
    heights = Rendition.objects.filter(
        media_file__in=files, status=RenditionStatus.READY, height__isnull=False
    ).values_list("height", flat=True)
    within = [height for height in heights if height and height <= ceiling]
    return max(within, default=0)


def _languages(tracks: Any) -> list[str]:
    found: dict[str, None] = {}
    for track in tracks or []:
        if isinstance(track, dict):
            language = str(track.get("language") or "").strip()
            if language and language != "und":
                found.setdefault(language, None)
    return list(found)


def _tracks(files: QuerySet[MediaFile]) -> dict[str, list[str]]:
    """Audio and subtitle languages of the primary playable file."""
    summary = (
        files.filter(playable_files())
        .order_by("-is_primary", "created_at")
        .values_list("probe_summary", flat=True)
        .first()
    )
    summary = summary if isinstance(summary, dict) else {}
    return {
        "audio": _languages(summary.get("audio")),
        "subtitles": _languages(summary.get("subtitles")),
    }


def _visible_category_ids(scope: CustomerScope, title: Movie | Series) -> set[UUID]:
    ids = [category.pk for category in title.categories.all()]
    return set(visible_categories(scope).filter(pk__in=ids).values_list("pk", flat=True))


class MovieDetailView(CatalogView):
    @extend_schema(
        operation_id="movies_retrieve",
        summary="A movie's page: details, people, your state, more like this",
        responses={200: MovieDetailSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        from apps.engagement import recommend, services  # noqa: PLC0415

        scope = self.scope()
        movie = queries.details(visible_movies(scope)).filter(pk=pk).first()
        if movie is None:
            raise not_found()
        files = MediaFile.objects.filter(movie=movie, removed_at__isnull=True)
        context = self.context(
            visible_category_ids=_visible_category_ids(scope, movie),
            quality_height=_best_height(files, scope.max_quality),
            tracks=_tracks(files),
            viewer=services.viewer_state(customer_of(request), movie),
            similar=recommend.more_like_this(scope, movie),
        )
        return Response(MovieDetailSerializer(movie, context=context).data)


class SeriesDetailView(CatalogView):
    @extend_schema(
        operation_id="series_retrieve",
        summary="A series' page: details, seasons, the episode to play next",
        responses={200: SeriesDetailSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        from apps.engagement import recommend, services  # noqa: PLC0415

        scope = self.scope()
        series = queries.details(visible_series(scope)).filter(pk=pk).first()
        if series is None:
            raise not_found()
        user = customer_of(request)
        files = MediaFile.objects.filter(
            episodes__season__series=series, removed_at__isnull=True
        ).distinct()
        upcoming = services.next_episode(user, series)
        context = self.context(
            visible_category_ids=_visible_category_ids(scope, series),
            quality_height=_best_height(files, scope.max_quality),
            tracks=_tracks(files),
            viewer=services.viewer_state(user, series),
            similar=recommend.more_like_this(scope, series),
            seasons=queries.seasons_with_episodes(series),
            next_episode=upcoming,
            episode_progress=services.episode_progress(user, [upcoming] if upcoming else []),
        )
        return Response(SeriesDetailSerializer(series, context=context).data)


class SeasonDetailView(CatalogView):
    @extend_schema(
        operation_id="series_season_retrieve",
        summary="A season's episodes you can watch, with your progress",
        responses={200: SeasonDetailSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID, number: int) -> Response:
        from apps.engagement import services  # noqa: PLC0415

        series = visible_series(self.scope()).filter(pk=pk).first()
        season = (
            Season.objects.filter(series=series, number=number)
            .select_related("series")
            .prefetch_related(queries.art(("poster",)))
            .first()
            if series is not None
            else None
        )
        episodes = queries.episodes_of(season) if season is not None else []
        if season is None or not episodes:
            raise not_found("season")
        context = self.context(
            episodes=episodes,
            episode_progress=services.episode_progress(customer_of(request), episodes),
        )
        return Response(SeasonDetailSerializer(season, context=context).data)


class EpisodeDetailView(CatalogView):
    @extend_schema(
        operation_id="episodes_retrieve",
        summary="An episode, its series, and the episodes before and after it",
        responses={200: EpisodeDetailSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        from apps.engagement import services  # noqa: PLC0415

        scope = self.scope()
        episode = (
            playable_episodes()
            .filter(pk=pk, season__series__in=visible_series(scope))
            .select_related("season")
            .prefetch_related(queries.art(("still",)))
            .first()
        )
        if episode is None:
            raise not_found("episode")
        series = queries.cards(Series.objects.filter(pk=episode.season.series_id)).get()
        order = list(
            playable_episodes()
            .filter(season__series=series)
            .order_by("season__number", "number")
            .values_list("pk", flat=True)
        )
        index = order.index(episode.pk)
        context = self.context(
            series=series,
            previous_id=order[index - 1] if index > 0 else None,
            next_id=order[index + 1] if index + 1 < len(order) else None,
            episode_progress=services.episode_progress(customer_of(request), [episode]),
        )
        return Response(EpisodeDetailSerializer(episode, context=context).data)


# --- Categories, genres, collections, people --------------------------------------------------


class CategoryListView(CatalogView):
    @extend_schema(
        operation_id="categories_list",
        summary="Categories that hold something you can watch",
        parameters=[OpenApiParameter("kind", str, enum=["vod", "series"])],
        responses={200: CategorySerializer(many=True), **problems(400, 401, 403)},
    )
    def get(self, request: Request) -> Response:
        scope = self.scope()
        kind = request.query_params.get("kind")
        if kind not in {None, "vod", "series"}:
            raise ProblemError(ErrorCode.VALIDATION_ERROR, "kind must be vod or series.")
        movies = Movie.categories.through.objects.filter(
            category=OuterRef("pk"), movie__in=visible_movies(scope)
        )
        series = Series.categories.through.objects.filter(
            category=OuterRef("pk"), series__in=visible_series(scope)
        )
        rows = (
            visible_categories(scope, kind)
            .filter(Exists(movies) | Exists(series))
            .order_by("kind", "sort", "name_en")
        )
        return Response(CategorySerializer(rows, many=True, context=self.context()).data)


class GenreListView(CatalogView):
    @extend_schema(
        operation_id="genres_list",
        summary="Genres of the titles you can watch",
        parameters=[OpenApiParameter("kind", str, enum=["movie", "series"])],
        responses={200: GenreSerializer(many=True), **problems(400, 401, 403)},
    )
    def get(self, request: Request) -> Response:
        scope = self.scope()
        kind = request.query_params.get("kind")
        if kind not in {None, "movie", "series"}:
            raise ProblemError(ErrorCode.VALIDATION_ERROR, "kind must be movie or series.")
        condition = Q()
        if kind in {None, "movie"}:
            condition |= Q(
                Exists(
                    Movie.genres.through.objects.filter(
                        genre=OuterRef("pk"), movie__in=visible_movies(scope)
                    )
                )
            )
        if kind in {None, "series"}:
            condition |= Q(
                Exists(
                    Series.genres.through.objects.filter(
                        genre=OuterRef("pk"), series__in=visible_series(scope)
                    )
                )
            )
        rows = Genre.objects.filter(condition).order_by("name_en")
        return Response(GenreSerializer(rows, many=True, context=self.context()).data)


class CollectionDetailView(CatalogView):
    @extend_schema(
        operation_id="collections_retrieve",
        summary="A collection's titles you can watch, in order",
        responses={200: CollectionSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, slug: str) -> Response:
        collection = Collection.objects.filter(slug=slug, published=True).first()
        if collection is None:
            raise not_found("collection")
        refs: list[queries.TitleRef] = [
            ("movie", movie_id) if movie_id else ("series", series_id)
            for movie_id, series_id in CollectionItem.objects.filter(collection=collection)
            .order_by("sort", "created_at")
            .values_list("movie_id", "series_id")
        ]
        items = queries.hydrate(self.scope(), refs)
        return Response(CollectionSerializer(collection, context=self.context(items=items)).data)


class PersonDetailView(CatalogView):
    @extend_schema(
        operation_id="people_retrieve",
        summary="A person and the titles you can watch with them",
        responses={200: PersonDetailSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        scope = self.scope()
        person = Person.objects.filter(pk=pk).prefetch_related(queries.art(("profile",))).first()
        if person is None:
            raise not_found("person")
        credits = list(
            Credit.objects.filter(person=person)
            .filter(Q(movie__in=visible_movies(scope)) | Q(series__in=visible_series(scope)))
            .values_list("movie_id", "series_id", "role", "character")
        )
        if not credits:
            raise not_found("person")
        refs: list[queries.TitleRef] = [
            ("movie", movie_id) if movie_id else ("series", series_id)
            for movie_id, series_id, _role, _character in credits
        ]
        titles = {queries.ref_of(title): title for title in queries.hydrate(scope, refs)}
        known_for = []
        for movie_id, series_id, role, character in credits:
            ref: queries.TitleRef = ("movie", movie_id) if movie_id else ("series", series_id)
            if ref in titles:
                known_for.append((titles[ref], role, character))
        known_for.sort(key=lambda row: (-(row[0].popularity or 0), str(row[0].pk)))
        context = self.context(known_for=known_for)
        return Response(PersonDetailSerializer(person, context=context).data)
