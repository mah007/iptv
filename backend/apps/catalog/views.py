"""Admin API of the catalogue (SPEC §8.3, docs/plans/poc.md slice 2): movies and series
(list, detail, edit, refresh metadata), the metadata review queue with a TMDB search for
manual matching, and categories (CRUD and reorder).

RBAC: `library.view` reads, `library.manage` edits, `library.review` decides reviews.
Every change is audited by the services it calls.
"""

from typing import Any, ClassVar
from uuid import UUID

from django.db.models import Count, Prefetch, Q, QuerySet
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import filters, generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasPermission, Requirements
from apps.catalog import services
from apps.catalog.filters import MovieFilter, ReviewFilter, SeriesFilter
from apps.catalog.models import (
    Category,
    Collection,
    CollectionItem,
    Credit,
    Episode,
    MatchReview,
    MediaFile,
    MediaImage,
    Movie,
    Season,
    Series,
)
from apps.catalog.serializers import (
    CandidateSerializer,
    CategoryReorderSerializer,
    CategorySerializer,
    CategoryWriteSerializer,
    CollectionSerializer,
    CollectionWriteSerializer,
    MovieDetailSerializer,
    MovieSummarySerializer,
    MovieUpdateSerializer,
    QueuedSerializer,
    ResolveSerializer,
    ReviewSerializer,
    SearchQuerySerializer,
    SeriesDetailSerializer,
    SeriesSummarySerializer,
    SeriesUpdateSerializer,
)
from apps.core.http import acting_user, client_ip
from apps.core.schema import problems

VIEW: tuple[str, ...] = ("library.view", "library.manage", "library.review")
TITLE_ORDERING = ("title", "year", "created_at", "updated_at", "rating", "popularity")


class AdminView(APIView):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {}


# --- Querysets (one query per relation; tests pin the counts) -------------------------------------


def active_files() -> QuerySet[MediaFile]:
    return MediaFile.objects.filter(removed_at__isnull=True)


def movie_list_queryset() -> QuerySet[Movie]:
    return Movie.objects.annotate(
        file_count=Count("files", filter=Q(files__removed_at__isnull=True), distinct=True)
    ).prefetch_related(
        "categories",
        Prefetch("images", queryset=MediaImage.objects.filter(kind="poster")),
    )


def series_list_queryset() -> QuerySet[Series]:
    on_disk = Q(seasons__episodes__files__removed_at__isnull=True)
    return Series.objects.annotate(
        file_count=Count("seasons__episodes__files", filter=on_disk, distinct=True),
        episode_count=Count("seasons__episodes", filter=on_disk, distinct=True),
    ).prefetch_related(
        "categories",
        Prefetch("images", queryset=MediaImage.objects.filter(kind="poster")),
    )


def _credits() -> Prefetch:
    return Prefetch("credits", queryset=Credit.objects.select_related("person"))


def movie_detail_queryset() -> QuerySet[Movie]:
    return Movie.objects.prefetch_related(
        "genres",
        "categories",
        "images",
        _credits(),
        Prefetch(
            "files",
            queryset=MediaFile.objects.select_related("library").order_by("storage_key"),
        ),
    )


def series_detail_queryset() -> QuerySet[Series]:
    episodes = Episode.objects.order_by("number").prefetch_related(
        "images",
        Prefetch("files", queryset=MediaFile.objects.select_related("library")),
    )
    seasons = Season.objects.order_by("number").prefetch_related(
        "images", Prefetch("episodes", queryset=episodes)
    )
    return Series.objects.prefetch_related(
        "genres", "categories", "images", _credits(), Prefetch("seasons", queryset=seasons)
    )


def review_queryset() -> QuerySet[MatchReview]:
    return MatchReview.objects.select_related(
        "media_file", "media_file__library", "decided_by"
    ).order_by("created_at", "id")


# --- Movies ---------------------------------------------------------------------------------------


_TITLE_LIST_PARAMS = [
    OpenApiParameter("search", str, description="Title, Arabic title or original title."),
    OpenApiParameter(
        "ordering",
        str,
        description="title, year, created_at, updated_at, rating or popularity; - for descending.",
    ),
]


@extend_schema_view(
    get=extend_schema(
        operation_id="movies_list",
        summary="List movies with poster, status and file count",
        parameters=_TITLE_LIST_PARAMS,
        responses={200: MovieSummarySerializer(many=True), **problems(400, 401, 403)},
    )
)
class MovieListView(generics.ListAPIView[Movie]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}
    serializer_class = MovieSummarySerializer
    filter_backends = (DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter)
    filterset_class = MovieFilter
    search_fields = ("title", "title_ar", "original_title")
    ordering_fields = TITLE_ORDERING
    ordering = ("title", "year")

    def get_queryset(self) -> QuerySet[Movie]:
        return movie_list_queryset().order_by("title", "year", "id")


class MovieDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "PATCH": "library.manage"}

    @extend_schema(
        operation_id="movies_retrieve",
        summary="A movie with metadata, artwork, credits and files",
        responses={200: MovieDetailSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        movie = get_object_or_404(movie_detail_queryset(), pk=pk)
        return Response(MovieDetailSerializer(movie).data)

    @extend_schema(
        operation_id="movies_update",
        summary="Edit a movie's metadata; edited fields are locked against refreshes",
        request=MovieUpdateSerializer,
        responses={200: MovieDetailSerializer, **problems(400, 401, 403, 404)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        movie = get_object_or_404(Movie, pk=pk)
        payload = MovieUpdateSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        services.update_title(
            movie, payload.validated_data, actor=acting_user(request), ip=client_ip(request)
        )
        return Response(MovieDetailSerializer(movie_detail_queryset().get(pk=pk)).data)


class MovieRefreshView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "library.manage"}

    @extend_schema(
        operation_id="movies_refresh_metadata",
        summary="Fetch the movie's metadata again (locked fields are kept)",
        request=None,
        responses={202: QueuedSerializer, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        movie = get_object_or_404(Movie, pk=pk)
        return _queue_refresh(request, "movie", movie)


# --- Series ---------------------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        operation_id="series_list",
        summary="List series with poster, status and episode counts",
        parameters=_TITLE_LIST_PARAMS,
        responses={200: SeriesSummarySerializer(many=True), **problems(400, 401, 403)},
    )
)
class SeriesListView(generics.ListAPIView[Series]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}
    serializer_class = SeriesSummarySerializer
    filter_backends = (DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter)
    filterset_class = SeriesFilter
    search_fields = ("title", "title_ar", "original_title")
    ordering_fields = TITLE_ORDERING
    ordering = ("title", "year")

    def get_queryset(self) -> QuerySet[Series]:
        return series_list_queryset().order_by("title", "year", "id")


class SeriesDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "PATCH": "library.manage"}

    @extend_schema(
        operation_id="series_retrieve",
        summary="A series with seasons, episodes, artwork, credits and files",
        responses={200: SeriesDetailSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        series = get_object_or_404(series_detail_queryset(), pk=pk)
        return Response(SeriesDetailSerializer(series).data)

    @extend_schema(
        operation_id="series_update",
        summary="Edit a series' metadata; edited fields are locked against refreshes",
        request=SeriesUpdateSerializer,
        responses={200: SeriesDetailSerializer, **problems(400, 401, 403, 404)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        series = get_object_or_404(Series, pk=pk)
        payload = SeriesUpdateSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        services.update_title(
            series, payload.validated_data, actor=acting_user(request), ip=client_ip(request)
        )
        return Response(SeriesDetailSerializer(series_detail_queryset().get(pk=pk)).data)


class SeriesRefreshView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "library.manage"}

    @extend_schema(
        operation_id="series_refresh_metadata",
        summary="Fetch the series' metadata again (locked fields are kept)",
        request=None,
        responses={202: QueuedSerializer, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        series = get_object_or_404(Series, pk=pk)
        return _queue_refresh(request, "series", series)


def _queue_refresh(request: Request, kind: str, title: Movie | Series) -> Response:
    from django.db import transaction  # noqa: PLC0415

    from apps.audit import services as audit  # noqa: PLC0415
    from apps.metadata import tasks  # noqa: PLC0415

    with transaction.atomic():
        audit.record(
            f"{kind}.refresh", actor=acting_user(request), target=title, ip=client_ip(request)
        )
        transaction.on_commit(lambda: tasks.refresh_title.delay(kind, str(title.pk)))
    return Response({"queued": True}, status=status.HTTP_202_ACCEPTED)


# --- Review queue ---------------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        operation_id="review_queue_list",
        summary="Files whose metadata match needs a decision (open by default)",
        parameters=[
            OpenApiParameter("status", str, description="open (default), resolved or skipped.")
        ],
        responses={200: ReviewSerializer(many=True), **problems(400, 401, 403)},
    )
)
class ReviewListView(generics.ListAPIView[MatchReview]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}
    serializer_class = ReviewSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ReviewFilter

    def get_queryset(self) -> QuerySet[MatchReview]:
        queryset = review_queryset()
        if "status" not in self.request.query_params:
            queryset = queryset.filter(status="open")
        return queryset


class ReviewDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {"GET": VIEW}

    @extend_schema(
        operation_id="review_queue_retrieve",
        summary="One review with the parsed name, the file and the scored candidates",
        responses={200: ReviewSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(ReviewSerializer(get_object_or_404(review_queryset(), pk=pk)).data)


class ReviewResolveView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "library.review"}

    @extend_schema(
        operation_id="review_queue_resolve",
        summary="Match the file to the chosen TMDB movie or series",
        request=ResolveSerializer,
        responses={200: ReviewSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        from apps.metadata import services as metadata  # noqa: PLC0415

        review = get_object_or_404(review_queryset(), pk=pk)
        payload = ResolveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        metadata.resolve_review(
            review,
            payload.validated_data["tmdb_id"],
            kind=payload.validated_data.get("kind"),
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(ReviewSerializer(review_queryset().get(pk=pk)).data)


class ReviewSkipView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "library.review"}

    @extend_schema(
        operation_id="review_queue_skip",
        summary="Set the review aside; the file stays unmatched",
        request=None,
        responses={200: ReviewSerializer, **problems(401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        from apps.metadata import services as metadata  # noqa: PLC0415

        review = get_object_or_404(review_queryset(), pk=pk)
        metadata.skip_review(review, actor=acting_user(request), ip=client_ip(request))
        return Response(ReviewSerializer(review_queryset().get(pk=pk)).data)


class MetadataSearchView(AdminView):
    required_permissions: ClassVar[Requirements] = {"GET": ("library.review", "library.manage")}

    @extend_schema(
        operation_id="metadata_search",
        summary="Search TMDB (or the fixtures) for manual matching",
        parameters=[SearchQuerySerializer],
        responses={200: CandidateSerializer(many=True), **problems(400, 401, 403)},
    )
    def get(self, request: Request) -> Response:
        from apps.metadata import services as metadata  # noqa: PLC0415

        query = SearchQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        found = metadata.search(
            query.validated_data["kind"],
            query.validated_data["query"],
            query.validated_data.get("year"),
        )
        results = [
            {**item, "poster_url": metadata.poster_preview_url(item.get("poster_path"))}
            for item in found
        ]
        return Response(CandidateSerializer(results, many=True).data)


# --- Categories -----------------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        operation_id="categories_list",
        summary="List categories, by kind and order",
        responses={200: CategorySerializer(many=True), **problems(400, 401, 403)},
    ),
    post=extend_schema(
        operation_id="categories_create",
        summary="Create a category",
        request=CategoryWriteSerializer,
        responses={201: CategorySerializer, **problems(400, 401, 403, 409)},
    ),
)
class CategoryListView(generics.ListCreateAPIView[Category]):
    """GET /api/v1/admin/categories: for access-profile editors and the catalogue."""

    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {
        "GET": ("customers.view", *VIEW),
        "POST": "library.manage",
    }
    serializer_class = CategorySerializer
    filter_backends = (DjangoFilterBackend, filters.SearchFilter)
    filterset_fields = ("kind",)
    search_fields = ("name_en", "name_ar", "slug")

    def get_queryset(self) -> QuerySet[Category]:
        return Category.objects.order_by("kind", "sort", "name_en")

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = CategoryWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        category = services.create_category(
            payload.validated_data, actor=acting_user(request), ip=client_ip(request)
        )
        return Response(CategorySerializer(category).data, status=status.HTTP_201_CREATED)


class CategoryDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {
        "GET": ("customers.view", *VIEW),
        "PATCH": "library.manage",
        "DELETE": "library.manage",
    }

    @extend_schema(
        operation_id="categories_retrieve",
        summary="One category",
        responses={200: CategorySerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(CategorySerializer(get_object_or_404(Category, pk=pk)).data)

    @extend_schema(
        operation_id="categories_update",
        summary="Change a category",
        request=CategoryWriteSerializer,
        responses={200: CategorySerializer, **problems(400, 401, 403, 404, 409)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        category = get_object_or_404(Category, pk=pk)
        payload = CategoryWriteSerializer(category, data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        services.update_category(
            category, payload.validated_data, actor=acting_user(request), ip=client_ip(request)
        )
        return Response(CategorySerializer(category).data)

    @extend_schema(
        operation_id="categories_delete",
        summary="Delete a category (titles and access profiles lose it)",
        responses={204: None, **problems(401, 403, 404, 409)},
    )
    def delete(self, request: Request, pk: UUID) -> Response:
        category = get_object_or_404(Category, pk=pk)
        services.delete_category(category, actor=acting_user(request), ip=client_ip(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


class CategoryReorderView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "library.manage"}

    @extend_schema(
        operation_id="categories_reorder",
        summary="Put the categories of one kind in the given order",
        request=CategoryReorderSerializer,
        responses={200: CategorySerializer(many=True), **problems(400, 401, 403)},
    )
    def post(self, request: Request) -> Response:
        payload = CategoryReorderSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        ordered = services.reorder_categories(
            payload.validated_data["kind"],
            payload.validated_data["ids"],
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(CategorySerializer(ordered, many=True).data)


# --- Collections (C1, ADR-0013) -------------------------------------------------------------------


def collection_queryset() -> QuerySet[Collection]:
    items = CollectionItem.objects.select_related("movie", "series").prefetch_related(
        Prefetch("movie__images", queryset=MediaImage.objects.filter(kind="poster")),
        Prefetch("series__images", queryset=MediaImage.objects.filter(kind="poster")),
    )
    return Collection.objects.prefetch_related(
        Prefetch("items", queryset=items.order_by("sort", "created_at"))
    ).order_by("sort", "name_en", "id")


@extend_schema_view(
    get=extend_schema(
        operation_id="collections_list",
        summary="Collections, in home-row order",
        responses={200: CollectionSerializer(many=True), **problems(400, 401, 403)},
    ),
    post=extend_schema(
        operation_id="collections_create",
        summary="Create a collection",
        request=CollectionWriteSerializer,
        responses={201: CollectionSerializer, **problems(400, 401, 403, 409)},
    ),
)
class CollectionListView(generics.ListCreateAPIView[Collection]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": VIEW, "POST": "library.manage"}
    serializer_class = CollectionSerializer
    filter_backends = (filters.SearchFilter,)
    search_fields = ("name_en", "name_ar", "slug")

    def get_queryset(self) -> QuerySet[Collection]:
        return collection_queryset()

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = CollectionWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        collection = services.create_collection(
            payload.validated_data, actor=acting_user(request), ip=client_ip(request)
        )
        body = CollectionSerializer(collection_queryset().get(pk=collection.pk)).data
        return Response(body, status=status.HTTP_201_CREATED)


class CollectionDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {
        "GET": VIEW,
        "PATCH": "library.manage",
        "DELETE": "library.manage",
    }

    @extend_schema(
        operation_id="collections_retrieve",
        summary="One collection with its titles",
        responses={200: CollectionSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(CollectionSerializer(get_object_or_404(collection_queryset(), pk=pk)).data)

    @extend_schema(
        operation_id="collections_update",
        summary="Change a collection; `items` replaces its titles in order",
        request=CollectionWriteSerializer,
        responses={200: CollectionSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        collection = get_object_or_404(Collection, pk=pk)
        payload = CollectionWriteSerializer(collection, data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        services.update_collection(
            collection, payload.validated_data, actor=acting_user(request), ip=client_ip(request)
        )
        return Response(CollectionSerializer(collection_queryset().get(pk=pk)).data)

    @extend_schema(
        operation_id="collections_delete",
        summary="Delete a collection (its titles stay)",
        responses={204: None, **problems(401, 403, 404)},
    )
    def delete(self, request: Request, pk: UUID) -> Response:
        collection = get_object_or_404(Collection, pk=pk)
        services.delete_collection(collection, actor=acting_user(request), ip=client_ip(request))
        return Response(status=status.HTTP_204_NO_CONTENT)
