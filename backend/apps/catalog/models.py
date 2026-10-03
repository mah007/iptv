"""The catalogue (SPEC §6 catalog, POC subset in docs/plans/poc.md): categories, genres,
movies, series with seasons and episodes, people and credits, artwork, media files and the
metadata review queue.

Primary keys are UUIDv7 (`BaseModel`). Movies, series and episodes also get `xc_id`, an
integer from the one sequence `catalog_xc_id_seq`, because Xtream clients identify streams
and series by integers (SPEC §7.5); categories keep their own sequence. `storage_key`
(the library-relative path of a file) is internal and never serialized.
"""

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Upper

from apps.core.db import NextVal
from apps.core.models import BaseModel

CATEGORY_XC_ID_SEQUENCE = "catalog_category_xc_id_seq"
#: Movies, series and episodes share it, so an Xtream id never names two things.
CATALOG_XC_ID_SEQUENCE = "catalog_xc_id_seq"


class CategoryKind(models.TextChoices):
    VOD = "vod", "Movies"
    SERIES = "series", "Series"
    LIVE = "live", "Live TV"


class Category(BaseModel):
    """A browse category for movies, series or live channels (SPEC §6 catalog).

    Customer access profiles restrict playback to categories (SPEC §7.4). `xc_id` is
    the integer id Xtream clients see as `category_id`.
    """

    xc_id = models.BigIntegerField(unique=True, db_default=NextVal(CATEGORY_XC_ID_SEQUENCE))
    kind = models.CharField(max_length=8, choices=CategoryKind.choices)
    name_en = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    sort = models.IntegerField(default=0)
    is_adult = models.BooleanField(default=False)
    visible_in_xtream = models.BooleanField(default=True)
    icon = models.CharField(max_length=64, blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )

    class Meta:
        ordering = ("kind", "sort", "name_en")
        verbose_name_plural = "categories"
        constraints = (
            models.UniqueConstraint(fields=("kind", "slug"), name="catalog_category_kind_slug"),
        )

    def __str__(self) -> str:
        return f"{self.kind}:{self.slug}"

    def clean(self) -> None:
        if self.parent_id is not None:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "A category cannot be its own parent."})
            if self.parent is not None and self.parent.kind != self.kind:
                raise ValidationError({"parent": "The parent must be of the same kind."})


class Genre(BaseModel):
    """A TMDB genre; mapped to categories by `metadata.genre_category_map`."""

    tmdb_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    name_en = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ("name_en",)

    def __str__(self) -> str:
        return self.name_en


class TitleStatus(models.TextChoices):
    """Lifecycle of a movie or series (SPEC §6). `ready` needs a playable file."""

    PROCESSING = "processing", "Processing"
    REVIEW = "review", "Needs review"
    READY = "ready", "Ready"
    HIDDEN = "hidden", "Hidden"
    LICENSE_EXPIRED = "license_expired", "License expired"


class MetadataSource(models.TextChoices):
    """Where a title's metadata came from. `synthetic` is fixture mode (no TMDB key):
    hand-written sample data, labelled as such in the admin."""

    TMDB = "tmdb", "TMDB"
    SYNTHETIC = "synthetic", "Synthetic fixtures"
    MANUAL = "manual", "Manual"


class EpisodeOrdering(models.TextChoices):
    TMDB_DEFAULT = "tmdb_default", "TMDB default"
    TVDB_AIRED = "tvdb_aired", "TVDB aired"
    TVDB_DVD = "tvdb_dvd", "TVDB DVD"


class Title(BaseModel):
    """Fields movies and series share (SPEC §6 Movie; Series mirrors it)."""

    xc_id = models.BigIntegerField(
        unique=True, editable=False, db_default=NextVal(CATALOG_XC_ID_SEQUENCE)
    )
    tmdb_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    imdb_id = models.CharField(max_length=16, blank=True)
    title = models.CharField(max_length=255)
    title_ar = models.CharField(max_length=255, blank=True)
    original_title = models.CharField(max_length=255, blank=True)
    alt_titles = ArrayField(models.CharField(max_length=255), default=list, blank=True)
    overview = models.TextField(blank=True)
    overview_ar = models.TextField(blank=True)
    tagline = models.CharField(max_length=500, blank=True)
    tagline_ar = models.CharField(max_length=500, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    rating = models.FloatField(null=True, blank=True)  # 0-10
    vote_count = models.PositiveIntegerField(default=0)
    certification = models.CharField(max_length=16, blank=True)
    original_language = models.CharField(max_length=8, blank=True)
    countries = ArrayField(models.CharField(max_length=2), default=list, blank=True)
    trailer_youtube_key = models.CharField(max_length=32, blank=True)
    popularity = models.FloatField(default=0.0)
    status = models.CharField(
        max_length=16, choices=TitleStatus.choices, default=TitleStatus.PROCESSING
    )
    featured = models.BooleanField(default=False)
    rights_holder = models.CharField(max_length=255, blank=True)
    license_ref = models.CharField(max_length=255, blank=True)
    license_expires_at = models.DateTimeField(null=True, blank=True)
    metadata_source = models.CharField(
        max_length=16, choices=MetadataSource.choices, default=MetadataSource.TMDB
    )
    #: Fields an admin edited: a metadata refresh leaves them alone (SPEC §7.2 step 9).
    metadata_locked_fields = ArrayField(models.CharField(max_length=64), default=list, blank=True)
    metadata_refreshed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title


class Movie(Title):
    release_date = models.DateField(null=True, blank=True)
    runtime_min = models.PositiveSmallIntegerField(null=True, blank=True)
    categories = models.ManyToManyField(Category, blank=True, related_name="movies")
    genres = models.ManyToManyField(Genre, blank=True, related_name="movies")

    class Meta:
        ordering = ("title", "year")
        indexes = (
            models.Index(fields=("status", "-created_at"), name="catalog_movie_status_created"),
            GinIndex(OpClass(Upper("title"), name="gin_trgm_ops"), name="catalog_movie_title_trgm"),
            GinIndex(
                OpClass(Upper("title_ar"), name="gin_trgm_ops"), name="catalog_movie_title_ar_trgm"
            ),
        )


class Series(Title):
    tvdb_id = models.PositiveIntegerField(null=True, blank=True)
    first_air_date = models.DateField(null=True, blank=True)
    last_air_date = models.DateField(null=True, blank=True)
    episode_run_time = models.PositiveSmallIntegerField(null=True, blank=True)
    episode_ordering = models.CharField(
        max_length=16, choices=EpisodeOrdering.choices, default=EpisodeOrdering.TMDB_DEFAULT
    )
    categories = models.ManyToManyField(Category, blank=True, related_name="series")
    genres = models.ManyToManyField(Genre, blank=True, related_name="series")

    class Meta:
        ordering = ("title", "year")
        verbose_name_plural = "series"
        indexes = (
            models.Index(fields=("status", "-created_at"), name="catalog_series_status_created"),
            GinIndex(
                OpClass(Upper("title"), name="gin_trgm_ops"), name="catalog_series_title_trgm"
            ),
            GinIndex(
                OpClass(Upper("title_ar"), name="gin_trgm_ops"),
                name="catalog_series_title_ar_trgm",
            ),
        )


class Season(BaseModel):
    """A season of a series; specials are season 0."""

    series = models.ForeignKey(Series, on_delete=models.CASCADE, related_name="seasons")
    number = models.PositiveSmallIntegerField()
    tmdb_id = models.PositiveIntegerField(null=True, blank=True)
    name = models.CharField(max_length=255, blank=True)
    name_ar = models.CharField(max_length=255, blank=True)
    overview = models.TextField(blank=True)
    overview_ar = models.TextField(blank=True)
    air_date = models.DateField(null=True, blank=True)
    #: Episodes the provider lists for the season (not only those with files).
    episode_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("series", "number")
        constraints = (
            models.UniqueConstraint(fields=("series", "number"), name="catalog_season_number"),
        )

    def __str__(self) -> str:
        return f"{self.series_id} S{self.number:02d}"


class Episode(BaseModel):
    xc_id = models.BigIntegerField(
        unique=True, editable=False, db_default=NextVal(CATALOG_XC_ID_SEQUENCE)
    )
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="episodes")
    number = models.PositiveSmallIntegerField()
    absolute_number = models.PositiveIntegerField(null=True, blank=True)
    tmdb_id = models.PositiveIntegerField(null=True, blank=True)
    title = models.CharField(max_length=255, blank=True)
    title_ar = models.CharField(max_length=255, blank=True)
    overview = models.TextField(blank=True)
    overview_ar = models.TextField(blank=True)
    air_date = models.DateField(null=True, blank=True)
    runtime_min = models.PositiveSmallIntegerField(null=True, blank=True)
    rating = models.FloatField(null=True, blank=True)
    #: The provider's still (the `still` MediaImage holds the stored renditions).
    still_path = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("season", "number")
        constraints = (
            models.UniqueConstraint(fields=("season", "number"), name="catalog_episode_number"),
        )

    def __str__(self) -> str:
        return f"{self.season_id} E{self.number:02d}"


class Person(BaseModel):
    tmdb_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    name = models.CharField(max_length=255)
    name_ar = models.CharField(max_length=255, blank=True)
    #: The provider's profile picture path (downloaded on demand, not at ingest).
    profile_path = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("name",)
        verbose_name_plural = "people"

    def __str__(self) -> str:
        return self.name


class CreditRole(models.TextChoices):
    CAST = "cast", "Cast"
    DIRECTOR = "director", "Director"
    WRITER = "writer", "Writer"
    PRODUCER = "producer", "Producer"


class Credit(BaseModel):
    """A person's part in a movie or a series (exactly one of the two)."""

    movie = models.ForeignKey(
        Movie, null=True, blank=True, on_delete=models.CASCADE, related_name="credits"
    )
    series = models.ForeignKey(
        Series, null=True, blank=True, on_delete=models.CASCADE, related_name="credits"
    )
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="credits")
    role = models.CharField(max_length=16, choices=CreditRole.choices)
    character = models.CharField(max_length=255, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("role", "order")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(movie__isnull=False, series__isnull=True)
                | models.Q(movie__isnull=True, series__isnull=False),
                name="catalog_credit_one_title",
            ),
        )


class ImageKind(models.TextChoices):
    POSTER = "poster", "Poster"
    BACKDROP = "backdrop", "Backdrop"
    LOGO = "logo", "Logo"
    STILL = "still", "Still"
    PROFILE = "profile", "Profile"


class ImageSource(models.TextChoices):
    TMDB = "tmdb", "TMDB"
    UPLOAD = "upload", "Upload"


_IMAGE_OWNERS = ("movie", "series", "season", "episode", "person")


class MediaImage(BaseModel):
    """Stored artwork: one row per source image, a storage key per size and format.

    `sizes` is `{"w185": {"webp": "images/…", "avif": "images/…"}, …}`; keys are paths
    under the media volume that the edge serves at `media.<domain>/images/…`, named by
    content hash so they can be cached as immutable (SPEC §7.2 step 7).
    """

    movie = models.ForeignKey(
        Movie, null=True, blank=True, on_delete=models.CASCADE, related_name="images"
    )
    series = models.ForeignKey(
        Series, null=True, blank=True, on_delete=models.CASCADE, related_name="images"
    )
    season = models.ForeignKey(
        Season, null=True, blank=True, on_delete=models.CASCADE, related_name="images"
    )
    episode = models.ForeignKey(
        Episode, null=True, blank=True, on_delete=models.CASCADE, related_name="images"
    )
    person = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.CASCADE, related_name="images"
    )
    kind = models.CharField(max_length=16, choices=ImageKind.choices)
    language = models.CharField(max_length=8, blank=True)
    sizes = models.JSONField(default=dict)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    blurhash = models.CharField(max_length=64, blank=True)
    source = models.CharField(max_length=8, choices=ImageSource.choices, default=ImageSource.TMDB)
    #: The provider's file path (`/abc.jpg`), so a refresh does not download it again.
    source_path = models.CharField(max_length=255, blank=True)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ("kind", "-is_primary", "created_at")
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(movie__isnull=False, series__isnull=True, season__isnull=True,
                             episode__isnull=True, person__isnull=True)
                    | models.Q(movie__isnull=True, series__isnull=False, season__isnull=True,
                               episode__isnull=True, person__isnull=True)
                    | models.Q(movie__isnull=True, series__isnull=True, season__isnull=False,
                               episode__isnull=True, person__isnull=True)
                    | models.Q(movie__isnull=True, series__isnull=True, season__isnull=True,
                               episode__isnull=False, person__isnull=True)
                    | models.Q(movie__isnull=True, series__isnull=True, season__isnull=True,
                               episode__isnull=True, person__isnull=False)
                ),
                name="catalog_mediaimage_one_owner",
            ),
        )  # fmt: skip

    def __str__(self) -> str:
        return f"{self.kind}:{self.owner_label}"

    @property
    def owner_label(self) -> str:
        for name in _IMAGE_OWNERS:
            value = getattr(self, f"{name}_id")
            if value is not None:
                return f"{name}/{value}"
        return "none"


class HdrKind(models.TextChoices):
    SDR = "sdr", "SDR"
    HDR10 = "hdr10", "HDR10"
    HLG = "hlg", "HLG"
    DV = "dv", "Dolby Vision"


class FileState(models.TextChoices):
    """Where a file is in the ingest pipeline (SPEC §7.1-7.2)."""

    PENDING = "pending", "Waiting to be probed"
    MATCHING = "matching", "Waiting for a metadata match"
    MATCHED = "matched", "Matched"
    REVIEW = "review", "Needs review"
    ERROR = "error", "Error"


class MediaFile(BaseModel):
    """A video file in a library, owned by a movie or by one or more episodes
    (multi-episode files such as `S01E01E02` link several).

    `storage_key` is the path relative to the library root; it is internal and never
    serialized (the admin shows `relative_path`, which is the same value presented as
    library-relative). Removed files are soft-removed (`removed_at`), so their metadata
    survives a move back.
    """

    library = models.ForeignKey("library.Library", on_delete=models.CASCADE, related_name="files")
    movie = models.ForeignKey(
        Movie, null=True, blank=True, on_delete=models.SET_NULL, related_name="files"
    )
    episodes = models.ManyToManyField(Episode, blank=True, related_name="files")
    storage_key = models.CharField(max_length=1024)
    size = models.BigIntegerField(default=0)
    mtime = models.DateTimeField(null=True, blank=True)
    xxhash64 = models.CharField(max_length=16, blank=True)
    container = models.CharField(max_length=16, blank=True)
    duration_ms = models.BigIntegerField(null=True, blank=True)
    bitrate = models.BigIntegerField(null=True, blank=True)
    video_codec = models.CharField(max_length=32, blank=True)
    video_profile = models.CharField(max_length=64, blank=True)
    video_level = models.IntegerField(null=True, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    fps = models.FloatField(null=True, blank=True)
    hdr = models.CharField(max_length=8, choices=HdrKind.choices, blank=True)
    parse_result = models.JSONField(default=dict, blank=True)
    #: ffprobe's JSON without the input file name (P3 `ProbeResult.raw`).
    probe = models.JSONField(default=dict, blank=True)
    #: P3 `ProbeResult.summary()`: the columns above plus audio and subtitle tracks.
    probe_summary = models.JSONField(default=dict, blank=True)
    #: Plays as is in every client: a faststart MP4 the compat profile could copy
    #: (P3 `planner.is_direct_playable`). Slice 2's definition of "playable".
    direct_play = models.BooleanField(default=False)
    match_confidence = models.FloatField(null=True, blank=True)
    version_label = models.CharField(max_length=100, blank=True)
    is_primary = models.BooleanField(default=True)
    state = models.CharField(max_length=16, choices=FileState.choices, default=FileState.PENDING)
    error = models.CharField(max_length=500, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("library", "storage_key")
        constraints = (
            models.UniqueConstraint(
                fields=("library", "storage_key"), name="catalog_mediafile_library_key"
            ),
        )
        indexes = (
            models.Index(fields=("xxhash64",), name="catalog_mediafile_hash"),
            models.Index(fields=("library", "removed_at"), name="catalog_mediafile_active"),
            models.Index(fields=("state",), name="catalog_mediafile_state"),
        )

    def __str__(self) -> str:
        return f"file:{self.pk}"

    @property
    def relative_path(self) -> str:
        """The path inside its library, for display (never an absolute storage path)."""
        return self.storage_key


class ReviewStatus(models.TextChoices):
    OPEN = "open", "Open"
    RESOLVED = "resolved", "Resolved"
    SKIPPED = "skipped", "Skipped"


class ReviewKind(models.TextChoices):
    MOVIE = "movie", "Movie"
    TV = "tv", "Series"


class MatchReview(BaseModel):
    """A file whose metadata match needs a person (SPEC §6, §7.2 step 4): the top five
    candidates with their score breakdowns, or none."""

    media_file = models.ForeignKey(MediaFile, on_delete=models.CASCADE, related_name="reviews")
    kind = models.CharField(max_length=8, choices=ReviewKind.choices)
    #: `below_threshold`, `ambiguous`, `no_candidates` (scoring) or `classification`,
    #: `episode_not_found`, `numbering` (the file cannot be placed).
    reason = models.CharField(max_length=32)
    parse_result = models.JSONField(default=dict)
    candidates = models.JSONField(default=list)
    status = models.CharField(max_length=8, choices=ReviewStatus.choices, default=ReviewStatus.OPEN)
    chosen_provider_id = models.PositiveIntegerField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("media_file",),
                condition=models.Q(status="open"),
                name="catalog_matchreview_one_open",
            ),
        )
        indexes = (models.Index(fields=("status", "created_at"), name="catalog_review_status"),)

    def __str__(self) -> str:
        return f"review:{self.pk}"
