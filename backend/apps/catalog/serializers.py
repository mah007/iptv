"""Admin API representations of the catalogue (SPEC §8.3: titles, review queue,
categories). Files are shown by their library-relative path; storage paths never leave
the server. Artwork URLs point at the media edge (`MEDIA_BASE_URL/images/...`)."""

from typing import Any, ClassVar

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import User
from apps.catalog.models import (
    Category,
    CategoryKind,
    Credit,
    Episode,
    Genre,
    MatchReview,
    MediaFile,
    MediaImage,
    Movie,
    Person,
    ReviewKind,
    Season,
    Series,
    TitleStatus,
)
from apps.catalog.services import media_url
from apps.library.parsing import ParseResult

#: The size shown by default (poster grids, detail pages).
DISPLAY_SIZES = ("w500", "w780", "original", "w1280", "w185")


class CategoryBriefSerializer(serializers.ModelSerializer[Category]):
    class Meta:
        model = Category
        fields = ("id", "kind", "name_en", "name_ar")
        read_only_fields = fields


class CategorySerializer(serializers.ModelSerializer[Category]):
    class Meta:
        model = Category
        fields = (
            "id",
            "xc_id",
            "kind",
            "name_en",
            "name_ar",
            "slug",
            "sort",
            "is_adult",
            "visible_in_xtream",
            "icon",
            "parent",
        )
        read_only_fields = fields


class CategoryWriteSerializer(serializers.ModelSerializer[Category]):
    """Create (kind and names required; the slug defaults to one made from name_en) or
    change a category. The kind cannot change once titles may use the category."""

    slug = serializers.SlugField(max_length=100, required=False, allow_blank=False)

    class Meta:
        model = Category
        fields = (
            "kind",
            "name_en",
            "name_ar",
            "slug",
            "sort",
            "is_adult",
            "visible_in_xtream",
            "icon",
            "parent",
        )
        # The (kind, slug) uniqueness is the service's: 409 CONFLICT, and the slug is
        # optional (made from name_en).
        validators: ClassVar[list[Any]] = []

    def validate_kind(self, value: str) -> str:
        if self.instance is not None and value != self.instance.kind:
            raise serializers.ValidationError("A category's kind cannot change.")
        return value


class CategoryReorderSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=CategoryKind.choices)
    ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, max_length=1000)


# --- Artwork --------------------------------------------------------------------------------------

_SIZES_FIELD = serializers.DictField(
    child=serializers.DictField(child=serializers.URLField()),
    help_text='URLs per size and format: {"w500": {"webp": url, "avif": url}, ...}',
)


class ImageSerializer(serializers.ModelSerializer[MediaImage]):
    url = serializers.SerializerMethodField(help_text="The default display size, as WebP.")
    sizes = serializers.SerializerMethodField()

    class Meta:
        model = MediaImage
        fields = (
            "id",
            "kind",
            "language",
            "url",
            "sizes",
            "width",
            "height",
            "blurhash",
            "is_primary",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_url(self, image: MediaImage) -> str | None:
        return display_url(image)

    @extend_schema_field(_SIZES_FIELD)
    def get_sizes(self, image: MediaImage) -> dict[str, dict[str, str]]:
        sizes = image.sizes if isinstance(image.sizes, dict) else {}
        return {
            size: {fmt: media_url(key) for fmt, key in formats.items() if isinstance(key, str)}
            for size, formats in sizes.items()
            if isinstance(formats, dict)
        }


def display_url(image: MediaImage) -> str | None:
    sizes = image.sizes if isinstance(image.sizes, dict) else {}
    for size in DISPLAY_SIZES:
        formats = sizes.get(size)
        if isinstance(formats, dict):
            key = formats.get("webp") or next(iter(formats.values()), None)
            if isinstance(key, str):
                return media_url(key)
    return None


def pick_image(images: list[MediaImage], kind: str) -> MediaImage | None:
    """The primary image of `kind` among prefetched images, else the first of that kind."""
    of_kind = [image for image in images if image.kind == kind]
    return next((image for image in of_kind if image.is_primary), of_kind[0] if of_kind else None)


# --- Files ----------------------------------------------------------------------------------------


class LibraryBriefSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)


class AudioTrackSerializer(serializers.Serializer[Any]):
    stream_index = serializers.IntegerField()
    codec = serializers.CharField()
    channels = serializers.IntegerField()
    language = serializers.CharField()
    title = serializers.CharField(allow_null=True)
    default = serializers.BooleanField()
    forced = serializers.BooleanField()


class SubtitleTrackSerializer(serializers.Serializer[Any]):
    stream_index = serializers.IntegerField()
    codec = serializers.CharField()
    language = serializers.CharField()
    title = serializers.CharField(allow_null=True)
    default = serializers.BooleanField()
    forced = serializers.BooleanField()
    format = serializers.CharField(allow_null=True)
    text = serializers.BooleanField()


class FileSerializer(serializers.ModelSerializer[MediaFile]):
    """A media file as the admin sees it: its path inside its library, technical summary
    and pipeline state. Never the storage path."""

    library = LibraryBriefSerializer(read_only=True)
    relative_path = serializers.CharField(read_only=True)
    audio = serializers.SerializerMethodField()
    subtitles = serializers.SerializerMethodField()

    class Meta:
        model = MediaFile
        fields = (
            "id",
            "library",
            "relative_path",
            "size",
            "mtime",
            "container",
            "duration_ms",
            "bitrate",
            "video_codec",
            "video_profile",
            "video_level",
            "width",
            "height",
            "fps",
            "hdr",
            "audio",
            "subtitles",
            "direct_play",
            "match_confidence",
            "state",
            "error",
            "version_label",
            "is_primary",
            "removed_at",
            "created_at",
        )
        read_only_fields = fields

    @extend_schema_field(AudioTrackSerializer(many=True))
    def get_audio(self, file: MediaFile) -> list[dict[str, Any]]:
        tracks = (file.probe_summary or {}).get("audio") or []
        return [t for t in tracks if isinstance(t, dict)]

    @extend_schema_field(SubtitleTrackSerializer(many=True))
    def get_subtitles(self, file: MediaFile) -> list[dict[str, Any]]:
        tracks = (file.probe_summary or {}).get("subtitles") or []
        return [t for t in tracks if isinstance(t, dict)]


# --- Titles ---------------------------------------------------------------------------------------


class GenreSerializer(serializers.ModelSerializer[Genre]):
    class Meta:
        model = Genre
        fields = ("id", "tmdb_id", "name_en", "name_ar")
        read_only_fields = fields


class PersonSerializer(serializers.ModelSerializer[Person]):
    class Meta:
        model = Person
        fields = ("id", "tmdb_id", "name", "name_ar")
        read_only_fields = fields


class CreditSerializer(serializers.ModelSerializer[Credit]):
    person = PersonSerializer(read_only=True)

    class Meta:
        model = Credit
        fields = ("person", "role", "character", "order")
        read_only_fields = fields


class TitleSummaryFields(serializers.Serializer[Any]):
    """Poster and figures shared by the movie and series lists."""

    poster = serializers.SerializerMethodField()
    has_arabic_overview = serializers.SerializerMethodField()
    synthetic = serializers.SerializerMethodField(
        help_text="Metadata from the offline sample fixtures (no TMDB key), not from TMDB."
    )

    @extend_schema_field(ImageSerializer(allow_null=True))
    def get_poster(self, title: Movie | Series) -> dict[str, Any] | None:
        image = pick_image(list(title.images.all()), "poster")
        return ImageSerializer(image).data if image is not None else None

    def get_has_arabic_overview(self, title: Movie | Series) -> bool:
        return bool(title.overview_ar)

    def get_synthetic(self, title: Movie | Series) -> bool:
        return title.metadata_source == "synthetic"


_SUMMARY_FIELDS = (
    "id",
    "xc_id",
    "tmdb_id",
    "title",
    "title_ar",
    "original_title",
    "year",
    "status",
    "rating",
    "popularity",
    "metadata_source",
    "synthetic",
    "poster",
    "categories",
    "file_count",
    "has_arabic_overview",
    "created_at",
    "updated_at",
)


class MovieSummarySerializer(TitleSummaryFields, serializers.ModelSerializer[Movie]):
    categories = CategoryBriefSerializer(many=True, read_only=True)
    file_count = serializers.IntegerField(read_only=True, help_text="Files present on disk.")

    class Meta:
        model = Movie
        fields = (*_SUMMARY_FIELDS, "runtime_min")
        read_only_fields = fields


class SeriesSummarySerializer(TitleSummaryFields, serializers.ModelSerializer[Series]):
    categories = CategoryBriefSerializer(many=True, read_only=True)
    file_count = serializers.IntegerField(read_only=True, help_text="Files present on disk.")
    episode_count = serializers.IntegerField(
        read_only=True, help_text="Episodes with a file present on disk."
    )

    class Meta:
        model = Series
        fields = (*_SUMMARY_FIELDS, "episode_count")
        read_only_fields = fields


_DETAIL_FIELDS = (
    "id",
    "xc_id",
    "tmdb_id",
    "imdb_id",
    "title",
    "title_ar",
    "original_title",
    "alt_titles",
    "overview",
    "overview_ar",
    "tagline",
    "tagline_ar",
    "year",
    "rating",
    "vote_count",
    "certification",
    "original_language",
    "countries",
    "trailer_youtube_key",
    "popularity",
    "status",
    "featured",
    "rights_holder",
    "license_ref",
    "license_expires_at",
    "metadata_source",
    "synthetic",
    "metadata_locked_fields",
    "metadata_refreshed_at",
    "genres",
    "categories",
    "images",
    "credits",
    "created_at",
    "updated_at",
)


class TitleDetailFields(serializers.Serializer[Any]):
    genres = GenreSerializer(many=True, read_only=True)
    categories = CategoryBriefSerializer(many=True, read_only=True)
    images = ImageSerializer(many=True, read_only=True)
    credits = CreditSerializer(many=True, read_only=True)
    synthetic = serializers.SerializerMethodField(
        help_text="Metadata from the offline sample fixtures (no TMDB key), not from TMDB."
    )

    def get_synthetic(self, title: Movie | Series) -> bool:
        return title.metadata_source == "synthetic"


class MovieDetailSerializer(TitleDetailFields, serializers.ModelSerializer[Movie]):
    files = serializers.SerializerMethodField()

    class Meta:
        model = Movie
        fields = (*_DETAIL_FIELDS, "release_date", "runtime_min", "files")
        read_only_fields = fields

    @extend_schema_field(FileSerializer(many=True))
    def get_files(self, movie: Movie) -> list[dict[str, Any]]:
        return list(FileSerializer(movie.files.all(), many=True).data)


class EpisodeSerializer(serializers.ModelSerializer[Episode]):
    still = serializers.SerializerMethodField()
    files = FileSerializer(many=True, read_only=True)

    class Meta:
        model = Episode
        fields = (
            "id",
            "xc_id",
            "number",
            "absolute_number",
            "title",
            "title_ar",
            "overview",
            "overview_ar",
            "air_date",
            "runtime_min",
            "rating",
            "still",
            "files",
        )
        read_only_fields = fields

    @extend_schema_field(ImageSerializer(allow_null=True))
    def get_still(self, episode: Episode) -> dict[str, Any] | None:
        image = pick_image(list(episode.images.all()), "still")
        return ImageSerializer(image).data if image is not None else None


class SeasonSerializer(serializers.ModelSerializer[Season]):
    poster = serializers.SerializerMethodField()
    episodes = EpisodeSerializer(many=True, read_only=True)

    class Meta:
        model = Season
        fields = (
            "id",
            "number",
            "name",
            "name_ar",
            "overview",
            "overview_ar",
            "air_date",
            "episode_count",
            "poster",
            "episodes",
        )
        read_only_fields = fields

    @extend_schema_field(ImageSerializer(allow_null=True))
    def get_poster(self, season: Season) -> dict[str, Any] | None:
        image = pick_image(list(season.images.all()), "poster")
        return ImageSerializer(image).data if image is not None else None


class SeriesDetailSerializer(TitleDetailFields, serializers.ModelSerializer[Series]):
    seasons = SeasonSerializer(many=True, read_only=True)

    class Meta:
        model = Series
        fields = (
            *_DETAIL_FIELDS,
            "tvdb_id",
            "first_air_date",
            "last_air_date",
            "episode_run_time",
            "episode_ordering",
            "seasons",
        )
        read_only_fields = fields


#: An admin hides a title or shows it again (its files then decide the status).
VISIBILITY_CHOICES = [
    (TitleStatus.HIDDEN.value, "Hide the title"),
    (TitleStatus.READY.value, "Show it again (its files decide the status)"),
]


class TitleUpdateSerializer(serializers.Serializer[Any]):
    """An admin edit. Each metadata field given is locked against refreshes; `status`
    hides or shows the title; `metadata_locked_fields` sets the locks explicitly."""

    title = serializers.CharField(max_length=255, required=False)
    title_ar = serializers.CharField(max_length=255, required=False, allow_blank=True)
    original_title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    overview = serializers.CharField(required=False, allow_blank=True)
    overview_ar = serializers.CharField(required=False, allow_blank=True)
    tagline = serializers.CharField(max_length=500, required=False, allow_blank=True)
    tagline_ar = serializers.CharField(max_length=500, required=False, allow_blank=True)
    year = serializers.IntegerField(min_value=1870, max_value=2200, required=False, allow_null=True)
    rating = serializers.FloatField(min_value=0, max_value=10, required=False, allow_null=True)
    certification = serializers.CharField(max_length=16, required=False, allow_blank=True)
    trailer_youtube_key = serializers.CharField(max_length=32, required=False, allow_blank=True)
    featured = serializers.BooleanField(required=False)
    rights_holder = serializers.CharField(max_length=255, required=False, allow_blank=True)
    license_ref = serializers.CharField(max_length=255, required=False, allow_blank=True)
    license_expires_at = serializers.DateTimeField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=VISIBILITY_CHOICES, required=False)
    categories = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(), many=True, required=False
    )
    metadata_locked_fields = serializers.ListField(
        child=serializers.CharField(max_length=64), required=False, max_length=64
    )


class MovieUpdateSerializer(TitleUpdateSerializer):
    release_date = serializers.DateField(required=False, allow_null=True)
    runtime_min = serializers.IntegerField(
        min_value=1, max_value=2000, required=False, allow_null=True
    )


class SeriesUpdateSerializer(TitleUpdateSerializer):
    first_air_date = serializers.DateField(required=False, allow_null=True)
    episode_run_time = serializers.IntegerField(
        min_value=1, max_value=600, required=False, allow_null=True
    )


class QueuedSerializer(serializers.Serializer[Any]):
    queued = serializers.BooleanField()


# --- Review queue ---------------------------------------------------------------------------------


class ProviderIdsSerializer(serializers.Serializer[Any]):
    tmdb = serializers.IntegerField(allow_null=True)
    imdb = serializers.CharField(allow_null=True)
    tvdb = serializers.IntegerField(allow_null=True)


class ParseSerializer(serializers.Serializer[Any]):
    """What the file name says (P2 `ParseResult.to_json`)."""

    kind = serializers.CharField()
    title = serializers.CharField(allow_null=True)
    year = serializers.IntegerField(allow_null=True)
    alternative_title = serializers.CharField(allow_null=True)
    season = serializers.IntegerField(allow_null=True)
    episodes = serializers.ListField(child=serializers.IntegerField())
    absolute_episode = serializers.IntegerField(allow_null=True)
    air_date = serializers.DateField(allow_null=True)
    episode_title = serializers.CharField(allow_null=True)
    edition = serializers.CharField(allow_null=True)
    part = serializers.IntegerField(allow_null=True)
    resolution = serializers.CharField(allow_null=True)
    source = serializers.CharField(allow_null=True)  # type: ignore[assignment]
    codec = serializers.CharField(allow_null=True)
    languages = serializers.ListField(child=serializers.CharField())
    subtitle_languages = serializers.ListField(child=serializers.CharField())
    provider_ids = ProviderIdsSerializer()
    container = serializers.CharField(allow_null=True)
    ambiguous = serializers.BooleanField()


class BreakdownSerializer(serializers.Serializer[Any]):
    """Each signal's score from 0 to 1 (null: not measurable); SPEC §7.2 step 4."""

    title = serializers.FloatField()
    year = serializers.FloatField(allow_null=True)
    runtime = serializers.FloatField(allow_null=True)
    popularity = serializers.FloatField(allow_null=True)
    matched_title = serializers.CharField()


class CandidateSerializer(serializers.Serializer[Any]):
    provider = serializers.CharField()
    kind = serializers.ChoiceField(choices=(("movie", "Movie"), ("tv", "Series")))
    id = serializers.IntegerField(help_text="The TMDB id.")
    title = serializers.CharField()
    original_title = serializers.CharField(allow_null=True)
    year = serializers.IntegerField(allow_null=True)
    runtime_min = serializers.IntegerField(allow_null=True, required=False)
    popularity = serializers.FloatField()
    overview = serializers.CharField(allow_blank=True)
    poster_url = serializers.URLField(
        allow_null=True, help_text="A TMDB thumbnail; null in fixture mode."
    )
    score = serializers.FloatField(required=False)
    breakdown = BreakdownSerializer(required=False)


class ReviewFileSerializer(serializers.ModelSerializer[MediaFile]):
    library = LibraryBriefSerializer(read_only=True)
    relative_path = serializers.CharField(read_only=True)

    class Meta:
        model = MediaFile
        fields = (
            "id",
            "library",
            "relative_path",
            "size",
            "container",
            "duration_ms",
            "video_codec",
            "width",
            "height",
            "hdr",
            "state",
        )
        read_only_fields = fields


class ReviewSerializer(serializers.ModelSerializer[MatchReview]):
    media_file = ReviewFileSerializer(read_only=True)
    parse_result = serializers.SerializerMethodField()
    candidates = serializers.SerializerMethodField()
    decided_by: serializers.SlugRelatedField[User] = serializers.SlugRelatedField(
        slug_field="username", read_only=True
    )

    class Meta:
        model = MatchReview
        fields = (
            "id",
            "kind",
            "reason",
            "status",
            "media_file",
            "parse_result",
            "candidates",
            "chosen_provider_id",
            "decided_by",
            "decided_at",
            "created_at",
        )
        read_only_fields = fields

    @extend_schema_field(ParseSerializer(allow_null=True))
    def get_parse_result(self, review: MatchReview) -> dict[str, Any] | None:
        data = review.parse_result
        if not isinstance(data, dict) or "kind" not in data:
            return None
        return ParseResult.from_json(data).to_json()

    @extend_schema_field(CandidateSerializer(many=True))
    def get_candidates(self, review: MatchReview) -> list[dict[str, Any]]:
        from apps.metadata.services import poster_preview_url  # noqa: PLC0415

        return [
            {**item, "poster_url": poster_preview_url(item.get("poster_path"))}
            for item in review.candidates or []
            if isinstance(item, dict)
        ]


class ResolveSerializer(serializers.Serializer[Any]):
    tmdb_id = serializers.IntegerField(min_value=1)
    kind = serializers.ChoiceField(
        choices=ReviewKind.choices,
        required=False,
        help_text="Override the review's kind (a file that is a movie, not an episode).",
    )


class SearchQuerySerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=ReviewKind.choices)
    query = serializers.CharField(max_length=200)
    year = serializers.IntegerField(min_value=1870, max_value=2200, required=False)
