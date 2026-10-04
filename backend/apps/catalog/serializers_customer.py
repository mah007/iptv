"""Customer API representations of the catalogue (SPEC §9, §10 Catalog).

Text comes in one language, the request's (`context["locale"]`, see `i18n`): Arabic
fields fall back to English when empty. Artwork is a set of WebP and AVIF URLs per size
on the media edge, with the blurhash the portal shows while they load. Storage paths
and library details never appear.

Querysets are prepared by `apps.catalog.queries` (artwork in `art`, people with their
profile pictures), so serializing never runs a query per item.
"""

from collections.abc import Iterable, Sequence
from typing import Any, Final

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.catalog.i18n import Locale, localized
from apps.catalog.models import (
    Category,
    Collection,
    Credit,
    CreditRole,
    Episode,
    Genre,
    MediaImage,
    Movie,
    Person,
    Season,
    Series,
)
from apps.catalog.services import media_url

POSTER_SIZES: Final = ("w500", "w185", "original")
BACKDROP_SIZES: Final = ("w1280", "w780", "original")
STILL_SIZES: Final = ("w500", "w185", "original")
LOGO_SIZES: Final = ("w500", "original", "w185")
PROFILE_SIZES: Final = ("w185", "w500", "original")
MAX_CAST: Final = 15


class ArtworkSerializer(serializers.Serializer[Any]):
    """One image: a default URL plus WebP and AVIF per size, and its blurhash."""

    url = serializers.URLField(help_text="The default size as WebP.")
    sizes = serializers.DictField(
        child=serializers.DictField(child=serializers.URLField()),
        help_text='{"w500": {"webp": url, "avif": url}, ...}',
    )
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    blurhash = serializers.CharField(allow_blank=True)


def artwork(image: MediaImage | None, preferred: Sequence[str]) -> dict[str, Any] | None:
    if image is None:
        return None
    stored = image.sizes if isinstance(image.sizes, dict) else {}
    sizes = {
        size: {fmt: media_url(key) for fmt, key in formats.items() if isinstance(key, str) and key}
        for size, formats in stored.items()
        if isinstance(formats, dict)
    }
    url = next(
        (
            sizes[size].get("webp") or next(iter(sizes[size].values()), None)
            for size in (*preferred, *sizes)
            if sizes.get(size)
        ),
        None,
    )
    if url is None:
        return None
    return {
        "url": url,
        "sizes": sizes,
        "width": image.width,
        "height": image.height,
        "blurhash": image.blurhash,
    }


def images_of(owner: Any) -> list[MediaImage]:
    """Prefetched artwork (`art`, else `images`), primary first within each kind."""
    images = getattr(owner, "art", None)
    if images is None:
        images = list(owner.images.all())
    return list(images)


def pick(images: Iterable[MediaImage], kind: str) -> MediaImage | None:
    of_kind = [image for image in images if image.kind == kind]
    return next((image for image in of_kind if image.is_primary), of_kind[0] if of_kind else None)


def _locale(serializer: serializers.Serializer[Any]) -> Locale:
    locale = serializer.context.get("locale", "en")
    return "ar" if locale == "ar" else "en"


_ARTWORK = ArtworkSerializer(allow_null=True)
#: Shared choice sets (named in config.urls_portal.SPECTACULAR_SETTINGS).
TITLE_TYPE_CHOICES: Final = (("movie", "Movie"), ("series", "Series"))
PLAYING_TYPE_CHOICES: Final = (("movie", "Movie"), ("episode", "Episode"))
THUMB_CHOICES: Final = (("up", "Up"), ("down", "Down"))


class LocalizedSerializer(serializers.Serializer[Any]):
    def text(self, obj: Any, field: str) -> str:
        return localized(obj, field, _locale(self))


# --- Small pieces -----------------------------------------------------------------------------


class GenreSerializer(LocalizedSerializer):
    id = serializers.UUIDField()
    name = serializers.SerializerMethodField()

    def get_name(self, genre: Genre) -> str:
        return (genre.name_ar or genre.name_en) if _locale(self) == "ar" else genre.name_en


class CategorySerializer(LocalizedSerializer):
    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=(("vod", "Movies"), ("series", "Series")))
    slug = serializers.SlugField()
    name = serializers.SerializerMethodField()
    sort = serializers.IntegerField()
    parent_id = serializers.UUIDField(allow_null=True)

    def get_name(self, category: Category) -> str:
        if _locale(self) == "ar":
            return category.name_ar or category.name_en
        return category.name_en


class PersonBriefSerializer(LocalizedSerializer):
    id = serializers.UUIDField()
    name = serializers.SerializerMethodField()
    profile = serializers.SerializerMethodField()

    def get_name(self, person: Person) -> str:
        return self.text(person, "name")

    @extend_schema_field(_ARTWORK)
    def get_profile(self, person: Person) -> dict[str, Any] | None:
        return artwork(pick(images_of(person), "profile"), PROFILE_SIZES)


class CastSerializer(PersonBriefSerializer):
    character = serializers.CharField(allow_blank=True)


# --- Cards and titles -------------------------------------------------------------------------


class TitleCardSerializer(LocalizedSerializer):
    """A movie or a series in a grid or a row."""

    type = serializers.SerializerMethodField()
    id = serializers.UUIDField()
    title = serializers.SerializerMethodField()
    original_title = serializers.CharField(allow_blank=True)
    year = serializers.IntegerField(allow_null=True)
    rating = serializers.FloatField(allow_null=True, help_text="TMDB rating, 0 to 10.")
    certification = serializers.CharField(allow_blank=True)
    runtime_min = serializers.SerializerMethodField()
    poster = serializers.SerializerMethodField()
    backdrop = serializers.SerializerMethodField()

    @extend_schema_field(serializers.ChoiceField(choices=TITLE_TYPE_CHOICES))
    def get_type(self, title: Movie | Series) -> str:
        return "movie" if isinstance(title, Movie) else "series"

    def get_title(self, title: Movie | Series) -> str:
        return self.text(title, "title")

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_runtime_min(self, title: Movie | Series) -> int | None:
        if isinstance(title, Movie):
            return title.runtime_min
        return title.episode_run_time

    @extend_schema_field(_ARTWORK)
    def get_poster(self, title: Movie | Series) -> dict[str, Any] | None:
        return artwork(pick(images_of(title), "poster"), POSTER_SIZES)

    @extend_schema_field(_ARTWORK)
    def get_backdrop(self, title: Movie | Series) -> dict[str, Any] | None:
        return artwork(pick(images_of(title), "backdrop"), BACKDROP_SIZES)


class HeroSerializer(TitleCardSerializer):
    """A featured title for the home carousel: backdrop, logo, overview."""

    overview = serializers.SerializerMethodField()
    tagline = serializers.SerializerMethodField()
    logo = serializers.SerializerMethodField()

    def get_overview(self, title: Movie | Series) -> str:
        return self.text(title, "overview")

    def get_tagline(self, title: Movie | Series) -> str:
        return self.text(title, "tagline")

    @extend_schema_field(_ARTWORK)
    def get_logo(self, title: Movie | Series) -> dict[str, Any] | None:
        return artwork(pick(images_of(title), "logo"), LOGO_SIZES)


class QualitySerializer(serializers.Serializer[Any]):
    height = serializers.IntegerField(help_text="The best height this customer can play.")
    badge = serializers.ChoiceField(
        choices=(("4K", "4K"), ("FHD", "FHD"), ("HD", "HD"), ("SD", "SD"))
    )


def quality_label(height: int) -> str:
    if height >= 2000:
        return "4K"
    if height >= 1000:
        return "FHD"
    if height >= 700:
        return "HD"
    return "SD"


class ProgressSerializer(serializers.Serializer[Any]):
    position_ms = serializers.IntegerField()
    duration_ms = serializers.IntegerField()
    completed = serializers.BooleanField()
    updated_at = serializers.DateTimeField()


class ViewerStateSerializer(serializers.Serializer[Any]):
    """The signed-in customer's relation to the title."""

    favorite = serializers.BooleanField()
    rating = serializers.ChoiceField(choices=THUMB_CHOICES, allow_null=True)
    progress = ProgressSerializer(allow_null=True)


class TrackLanguagesSerializer(serializers.Serializer[Any]):
    audio = serializers.ListField(child=serializers.CharField(), help_text="ISO 639-2 codes.")
    subtitles = serializers.ListField(child=serializers.CharField(), help_text="ISO 639-2 codes.")


class TitleDetailFields(TitleCardSerializer):
    overview = serializers.SerializerMethodField()
    tagline = serializers.SerializerMethodField()
    vote_count = serializers.IntegerField()
    original_language = serializers.CharField(allow_blank=True)
    countries = serializers.ListField(child=serializers.CharField())
    trailer_youtube_key = serializers.CharField(allow_blank=True)
    logo = serializers.SerializerMethodField()
    genres = serializers.SerializerMethodField()
    categories = serializers.SerializerMethodField()
    directors = serializers.SerializerMethodField()
    writers = serializers.SerializerMethodField()
    cast = serializers.SerializerMethodField()
    quality = serializers.SerializerMethodField()
    tracks = serializers.SerializerMethodField()
    viewer = serializers.SerializerMethodField()
    similar = serializers.SerializerMethodField()

    def get_overview(self, title: Movie | Series) -> str:
        return self.text(title, "overview")

    def get_tagline(self, title: Movie | Series) -> str:
        return self.text(title, "tagline")

    @extend_schema_field(_ARTWORK)
    def get_logo(self, title: Movie | Series) -> dict[str, Any] | None:
        return artwork(pick(images_of(title), "logo"), LOGO_SIZES)

    @extend_schema_field(GenreSerializer(many=True))
    def get_genres(self, title: Movie | Series) -> list[dict[str, Any]]:
        return list(GenreSerializer(title.genres.all(), many=True, context=self.context).data)

    @extend_schema_field(CategorySerializer(many=True))
    def get_categories(self, title: Movie | Series) -> list[dict[str, Any]]:
        visible: set[Any] = self.context.get("visible_category_ids", set())
        rows = [category for category in title.categories.all() if category.pk in visible]
        return list(CategorySerializer(rows, many=True, context=self.context).data)

    def _people(self, title: Movie | Series, role: str) -> list[Credit]:
        return [credit for credit in title.credits.all() if credit.role == role]

    @extend_schema_field(PersonBriefSerializer(many=True))
    def get_directors(self, title: Movie | Series) -> list[dict[str, Any]]:
        people = [credit.person for credit in self._people(title, CreditRole.DIRECTOR)]
        return list(PersonBriefSerializer(people, many=True, context=self.context).data)

    @extend_schema_field(PersonBriefSerializer(many=True))
    def get_writers(self, title: Movie | Series) -> list[dict[str, Any]]:
        people = [credit.person for credit in self._people(title, CreditRole.WRITER)]
        return list(PersonBriefSerializer(people, many=True, context=self.context).data)

    @extend_schema_field(CastSerializer(many=True))
    def get_cast(self, title: Movie | Series) -> list[dict[str, Any]]:
        credits = self._people(title, CreditRole.CAST)[:MAX_CAST]
        rows = [
            {**PersonBriefSerializer(credit.person, context=self.context).data,
             "character": credit.character}
            for credit in credits
        ]  # fmt: skip
        return rows

    @extend_schema_field(QualitySerializer(allow_null=True))
    def get_quality(self, title: Movie | Series) -> dict[str, Any] | None:
        height = int(self.context.get("quality_height") or 0)
        return {"height": height, "badge": quality_label(height)} if height else None

    @extend_schema_field(TrackLanguagesSerializer)
    def get_tracks(self, title: Movie | Series) -> dict[str, list[str]]:
        return self.context.get("tracks") or {"audio": [], "subtitles": []}

    @extend_schema_field(ViewerStateSerializer)
    def get_viewer(self, title: Movie | Series) -> dict[str, Any]:
        state: dict[str, Any] = self.context.get("viewer") or {}
        return {
            "favorite": bool(state.get("favorite")),
            "rating": state.get("rating"),
            "progress": state.get("progress"),
        }

    @extend_schema_field(TitleCardSerializer(many=True))
    def get_similar(self, title: Movie | Series) -> list[dict[str, Any]]:
        similar = self.context.get("similar") or []
        return list(TitleCardSerializer(similar, many=True, context=self.context).data)


class MovieDetailSerializer(TitleDetailFields):
    release_date = serializers.DateField(allow_null=True)


class EpisodeSerializer(LocalizedSerializer):
    id = serializers.UUIDField()
    season_number = serializers.SerializerMethodField()
    number = serializers.IntegerField()
    title = serializers.SerializerMethodField()
    overview = serializers.SerializerMethodField()
    air_date = serializers.DateField(allow_null=True)
    runtime_min = serializers.IntegerField(allow_null=True)
    rating = serializers.FloatField(allow_null=True)
    still = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()

    def get_season_number(self, episode: Episode) -> int:
        return int(episode.season.number)

    def get_title(self, episode: Episode) -> str:
        return self.text(episode, "title")

    def get_overview(self, episode: Episode) -> str:
        return self.text(episode, "overview")

    @extend_schema_field(_ARTWORK)
    def get_still(self, episode: Episode) -> dict[str, Any] | None:
        return artwork(pick(images_of(episode), "still"), STILL_SIZES)

    @extend_schema_field(ProgressSerializer(allow_null=True))
    def get_progress(self, episode: Episode) -> dict[str, Any] | None:
        progress: dict[Any, Any] = self.context.get("episode_progress") or {}
        found: dict[str, Any] | None = progress.get(episode.pk)
        return found


class SeasonBriefSerializer(LocalizedSerializer):
    number = serializers.IntegerField()
    name = serializers.SerializerMethodField()
    overview = serializers.SerializerMethodField()
    air_date = serializers.DateField(allow_null=True)
    episode_count = serializers.SerializerMethodField(help_text="Episodes you can watch now.")
    poster = serializers.SerializerMethodField()

    def get_name(self, season: Season) -> str:
        return self.text(season, "name") or ""

    def get_overview(self, season: Season) -> str:
        return self.text(season, "overview")

    def get_episode_count(self, season: Season) -> int:
        return int(getattr(season, "playable_count", 0) or 0)

    @extend_schema_field(_ARTWORK)
    def get_poster(self, season: Season) -> dict[str, Any] | None:
        return artwork(pick(images_of(season), "poster"), POSTER_SIZES)


class NextEpisodeSerializer(EpisodeSerializer):
    """What "Play" starts on a series page: the episode in progress, else the next one."""


class SeriesDetailSerializer(TitleDetailFields):
    first_air_date = serializers.DateField(allow_null=True)
    last_air_date = serializers.DateField(allow_null=True)
    seasons = serializers.SerializerMethodField()
    next_episode = serializers.SerializerMethodField()

    @extend_schema_field(SeasonBriefSerializer(many=True))
    def get_seasons(self, series: Series) -> list[dict[str, Any]]:
        seasons = self.context.get("seasons") or []
        return list(SeasonBriefSerializer(seasons, many=True, context=self.context).data)

    @extend_schema_field(NextEpisodeSerializer(allow_null=True))
    def get_next_episode(self, series: Series) -> dict[str, Any] | None:
        episode = self.context.get("next_episode")
        if episode is None:
            return None
        return dict(NextEpisodeSerializer(episode, context=self.context).data)


class SeriesRefSerializer(LocalizedSerializer):
    id = serializers.UUIDField()
    title = serializers.SerializerMethodField()

    def get_title(self, series: Series) -> str:
        return self.text(series, "title")


class SeasonDetailSerializer(SeasonBriefSerializer):
    series = serializers.SerializerMethodField()
    episodes = serializers.SerializerMethodField()

    @extend_schema_field(SeriesRefSerializer)
    def get_series(self, season: Season) -> dict[str, Any]:
        return dict(SeriesRefSerializer(season.series, context=self.context).data)

    @extend_schema_field(EpisodeSerializer(many=True))
    def get_episodes(self, season: Season) -> list[dict[str, Any]]:
        episodes = self.context.get("episodes") or []
        return list(EpisodeSerializer(episodes, many=True, context=self.context).data)

    def get_episode_count(self, season: Season) -> int:
        return len(self.context.get("episodes") or [])


class EpisodeDetailSerializer(EpisodeSerializer):
    series = serializers.SerializerMethodField()
    previous_id = serializers.SerializerMethodField()
    next_id = serializers.SerializerMethodField()

    @extend_schema_field(TitleCardSerializer)
    def get_series(self, episode: Episode) -> dict[str, Any]:
        return dict(TitleCardSerializer(self.context["series"], context=self.context).data)

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_previous_id(self, episode: Episode) -> Any:
        return self.context.get("previous_id")

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_next_id(self, episode: Episode) -> Any:
        return self.context.get("next_id")


# --- Collections and people -------------------------------------------------------------------


class CollectionSerializer(LocalizedSerializer):
    slug = serializers.SlugField()
    name = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()

    def get_name(self, collection: Collection) -> str:
        if _locale(self) == "ar":
            return collection.name_ar or collection.name_en
        return collection.name_en

    def get_description(self, collection: Collection) -> str:
        if _locale(self) == "ar" and collection.description_ar:
            return collection.description_ar
        return collection.description_en

    @extend_schema_field(TitleCardSerializer(many=True))
    def get_items(self, collection: Collection) -> list[dict[str, Any]]:
        items = self.context.get("items") or []
        return list(TitleCardSerializer(items, many=True, context=self.context).data)


class KnownForSerializer(serializers.Serializer[Any]):
    title = TitleCardSerializer()
    role = serializers.ChoiceField(choices=CreditRole.choices)
    character = serializers.CharField(allow_blank=True)


class PersonDetailSerializer(PersonBriefSerializer):
    known_for = serializers.SerializerMethodField()

    @extend_schema_field(KnownForSerializer(many=True))
    def get_known_for(self, person: Person) -> list[dict[str, Any]]:
        rows = self.context.get("known_for") or []
        return [
            {
                "title": TitleCardSerializer(title, context=self.context).data,
                "role": role,
                "character": character,
            }
            for title, role, character in rows
        ]
