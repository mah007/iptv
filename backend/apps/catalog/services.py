"""Catalogue services: title status, genre categories, admin edits of titles and
categories (SPEC §6, §7.2 steps 5 and 9, §8.3).

Every admin change is audited inside its transaction and announced with
`apps.catalog.signals.catalog_changed` once it commits.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import User
from apps.audit import services as audit
from apps.catalog.models import (
    Category,
    CategoryKind,
    Collection,
    CollectionItem,
    Episode,
    MediaFile,
    Movie,
    Series,
    Title,
    TitleStatus,
)
from apps.catalog.signals import catalog_changed, notify_catalog_changed
from apps.core.errors import ErrorCode, ProblemError, field_error
from apps.core.registry import DEFAULT_GENRE_CATEGORY_MAP
from apps.core.services import get_setting
from apps.media.models import PLAYABLE_KINDS, RenditionStatus

#: English and Arabic names of the categories the default genre map creates.
CATEGORY_NAMES: Final[Mapping[str, tuple[str, str]]] = {
    "action": ("Action", "أكشن"),
    "adventure": ("Adventure", "مغامرة"),
    "animation": ("Animation", "رسوم متحركة"),
    "comedy": ("Comedy", "كوميديا"),
    "crime": ("Crime", "جريمة"),
    "documentary": ("Documentary", "وثائقي"),
    "drama": ("Drama", "دراما"),
    "family": ("Family", "عائلي"),
    "fantasy": ("Fantasy", "فانتازيا"),
    "history": ("History", "تاريخي"),
    "horror": ("Horror", "رعب"),
    "music": ("Music", "موسيقى"),
    "mystery": ("Mystery", "غموض"),
    "romance": ("Romance", "رومانسي"),
    "sci-fi": ("Science Fiction", "خيال علمي"),
    "thriller": ("Thriller", "إثارة"),
    "war": ("War", "حرب"),
    "western": ("Western", "غرب أمريكي"),
    "kids": ("Kids", "أطفال"),
    "news": ("News", "أخبار"),
    "reality": ("Reality", "واقع"),
    "talk": ("Talk", "حوارات"),
}

#: Metadata fields an admin may edit; editing one locks it against refreshes.
EDITABLE_FIELDS: Final = (
    "title",
    "title_ar",
    "original_title",
    "overview",
    "overview_ar",
    "tagline",
    "tagline_ar",
    "year",
    "rating",
    "certification",
    "trailer_youtube_key",
    "featured",
    "rights_holder",
    "license_ref",
    "license_expires_at",
)
MOVIE_EDITABLE_FIELDS: Final = (*EDITABLE_FIELDS, "release_date", "runtime_min")
SERIES_EDITABLE_FIELDS: Final = (*EDITABLE_FIELDS, "first_air_date", "episode_run_time")
#: Lock names that are not model fields: the M2M and the admin's visibility choice.
EXTRA_LOCKS: Final = ("categories", "status")
_SORT_STEP: Final = 10


# --- Media URLs -----------------------------------------------------------------------------------


def media_url(key: str) -> str:
    """Public URL of a stored file under the media volume (`images/...`)."""
    return f"{settings.MEDIA_BASE_URL.rstrip('/')}/{key.lstrip('/')}"


# --- Status (SPEC §6: ready once the title has a playable file) -----------------------------------


def playable_files() -> Q:
    """Active files that play now: sources that play directly in every client, and
    files with a ready progressive rendition (compat MP4 or direct-play source;
    ADR-0010). Filter with `.distinct()` when the rows matter, since it joins
    renditions."""
    return Q(removed_at__isnull=True) & (
        Q(direct_play=True)
        | Q(renditions__status=RenditionStatus.READY, renditions__kind__in=PLAYABLE_KINDS)
    )


def _derived_status(title: Title, active: QuerySet[MediaFile], now: datetime) -> str:
    if title.license_expires_at is not None and title.license_expires_at <= now:
        return TitleStatus.LICENSE_EXPIRED
    if active.filter(playable_files()).exists():
        return TitleStatus.READY
    if not active.exists():
        return TitleStatus.HIDDEN  # SPEC §7.1: no files left, so hidden
    return TitleStatus.PROCESSING


def refresh_movie_status(movie: Movie) -> bool:
    """Recompute `movie.status` from its files unless an admin locked it; True if changed."""
    if "status" in movie.metadata_locked_fields:
        return False
    active = MediaFile.objects.filter(movie=movie, removed_at__isnull=True)
    status = _derived_status(movie, active, timezone.now())
    if status == movie.status:
        return False
    movie.status = status
    movie.save(update_fields=["status", "updated_at"])
    notify_catalog_changed("movie", [movie.pk])
    return True


def refresh_series_status(series: Series) -> bool:
    """`refresh_movie_status` for a series: any episode's files count."""
    if "status" in series.metadata_locked_fields:
        return False
    active = MediaFile.objects.filter(
        episodes__season__series=series, removed_at__isnull=True
    ).distinct()
    status = _derived_status(series, active, timezone.now())
    if status == series.status:
        return False
    series.status = status
    series.save(update_fields=["status", "updated_at"])
    notify_catalog_changed("series", [series.pk])
    return True


def refresh_status_of_files(file_ids: Iterable[UUID]) -> None:
    """Recompute the status of every title these files belong to, and announce the change
    even when the status stays: a series gains or loses playable episodes either way."""
    ids = list(file_ids)
    if not ids:
        return
    movies = list(Movie.objects.filter(files__id__in=ids).distinct())
    for movie in movies:
        refresh_movie_status(movie)
    series_list = list(Series.objects.filter(seasons__episodes__files__id__in=ids).distinct())
    for series in series_list:
        refresh_series_status(series)
    if movies:
        notify_catalog_changed("movie", [movie.pk for movie in movies])
    if series_list:
        notify_catalog_changed("series", [series.pk for series in series_list])


def series_of_episodes(episodes: Iterable[Episode]) -> list[Series]:
    ids = {episode.season.series_id for episode in episodes}
    return list(Series.objects.filter(pk__in=ids))


# --- Genre categories (SPEC §7.2 step 5) ----------------------------------------------------------


def genre_category_map() -> dict[str, str]:
    """The `metadata.genre_category_map` setting; the default when it is unreadable."""
    value = get_setting("metadata.genre_category_map")
    try:
        data = json.loads(str(value))
    except ValueError:
        return dict(DEFAULT_GENRE_CATEGORY_MAP)
    if not isinstance(data, dict):
        return dict(DEFAULT_GENRE_CATEGORY_MAP)
    return {str(key): str(slug) for key, slug in data.items()}


def categories_for_genres(kind: str, genres: Sequence[tuple[int, str, str]]) -> list[Category]:
    """The categories of `kind` that TMDB genres `(id, name_en, name_ar)` map to, created
    on first use (names from `CATEGORY_NAMES`, else the genre's own names)."""
    mapping = genre_category_map()
    wanted: dict[str, tuple[str, str]] = {}
    for genre_id, name_en, name_ar in genres:
        slug = mapping.get(str(genre_id))
        if slug and slug not in wanted:
            wanted[slug] = CATEGORY_NAMES.get(slug, (name_en, name_ar or name_en))
    if not wanted:
        return []
    existing = {c.slug: c for c in Category.objects.filter(kind=kind, slug__in=list(wanted))}
    for slug, (name_en, name_ar) in wanted.items():
        if slug in existing:
            continue
        try:
            with transaction.atomic():
                existing[slug] = Category.objects.create(
                    kind=kind,
                    slug=slug,
                    name_en=name_en[:100],
                    name_ar=name_ar[:100],
                    sort=_next_sort(kind),
                )
        except IntegrityError:  # created concurrently by another worker
            existing[slug] = Category.objects.get(kind=kind, slug=slug)
        notify_catalog_changed("category", [existing[slug].pk])
    return [existing[slug] for slug in wanted]


def _next_sort(kind: str) -> int:
    last = Category.objects.filter(kind=kind).order_by("-sort").values_list("sort", flat=True)
    return (last.first() or 0) + _SORT_STEP


# --- Admin edits of titles ------------------------------------------------------------------------


def _snapshot(title: Title, fields: Iterable[str]) -> dict[str, Any]:
    return {name: getattr(title, name) for name in fields}


def update_title(  # noqa: PLR0912 (one branch per kind of change)
    title: Movie | Series,
    changes: Mapping[str, Any],
    *,
    actor: User | None,
    ip: str | None,
) -> Movie | Series:
    """Apply an admin's edit. Each edited metadata field joins `metadata_locked_fields`,
    so a refresh keeps it (SPEC §7.2 step 9). `status` "hidden" hides the title and locks
    it; any other status unlocks it and lets the files decide again. `categories` replaces
    the categories and locks them. `metadata_locked_fields`, when given, sets the locks
    explicitly (after the edit's own locks)."""
    editable = MOVIE_EDITABLE_FIELDS if isinstance(title, Movie) else SERIES_EDITABLE_FIELDS
    kind = "movie" if isinstance(title, Movie) else "series"
    fields = [name for name in editable if name in changes]
    before = _snapshot(title, [*fields, "status", "metadata_locked_fields"])
    locks = list(title.metadata_locked_fields)
    with transaction.atomic():
        for name in fields:
            setattr(title, name, changes[name])
            if name not in locks:
                locks.append(name)
        if "categories" in changes:
            categories = list(changes["categories"])
            expected = CategoryKind.VOD if kind == "movie" else CategoryKind.SERIES
            if any(category.kind != expected for category in categories):
                raise ProblemError(
                    ErrorCode.VALIDATION_ERROR,
                    f"Categories of a {kind} must be of kind {expected}.",
                    field_errors={"categories": [f"Use {expected} categories."]},
                )
            title.categories.set(categories)
            if "categories" not in locks:
                locks.append("categories")
        if "status" in changes:
            if changes["status"] == TitleStatus.HIDDEN:
                title.status = TitleStatus.HIDDEN
                if "status" not in locks:
                    locks.append("status")
            elif "status" in locks:
                locks.remove("status")
        if "metadata_locked_fields" in changes:
            allowed = {*editable, *EXTRA_LOCKS}
            locks = [
                name for name in dict.fromkeys(changes["metadata_locked_fields"]) if name in allowed
            ]
        title.metadata_locked_fields = locks
        title.save()
        if isinstance(title, Movie):
            refresh_movie_status(title)
        else:
            refresh_series_status(title)
        after = _snapshot(title, [*fields, "status", "metadata_locked_fields"])
        if "categories" in changes:
            after["categories"] = sorted(str(c.pk) for c in title.categories.all())
        audit.record(f"{kind}.update", actor=actor, target=title, before=before, after=after, ip=ip)
        notify_catalog_changed(kind, [title.pk])
    return title


# --- Admin edits of categories --------------------------------------------------------------------


def create_category(data: Mapping[str, Any], *, actor: User | None, ip: str | None) -> Category:
    values = dict(data)
    if not values.get("slug"):
        values["slug"] = slugify(values.get("name_en", ""))[:100] or "category"
    if "sort" not in values:
        values["sort"] = _next_sort(values["kind"])
    with transaction.atomic():
        if Category.objects.filter(kind=values["kind"], slug=values["slug"]).exists():
            raise ProblemError(
                ErrorCode.CONFLICT,
                "A category of this kind already uses that slug.",
                field_errors={"slug": ["Already used by another category of this kind."]},
            )
        category = Category(**values)
        category.full_clean()
        category.save()
        audit.record(
            "category.create", actor=actor, target=category, after=_category_state(category), ip=ip
        )
        notify_catalog_changed("category", [category.pk])
    return category


def update_category(
    category: Category, data: Mapping[str, Any], *, actor: User | None, ip: str | None
) -> Category:
    before = _category_state(category)
    with transaction.atomic():
        for name, value in data.items():
            setattr(category, name, value)
        duplicate = (
            Category.objects.filter(kind=category.kind, slug=category.slug)
            .exclude(pk=category.pk)
            .exists()
        )
        if duplicate:
            raise ProblemError(
                ErrorCode.CONFLICT,
                "A category of this kind already uses that slug.",
                field_errors={"slug": ["Already used by another category of this kind."]},
            )
        category.full_clean()
        category.save()
        audit.record(
            "category.update",
            actor=actor,
            target=category,
            before=before,
            after=_category_state(category),
            ip=ip,
        )
        notify_catalog_changed("category", [category.pk])
    return category


def delete_category(category: Category, *, actor: User | None, ip: str | None) -> None:
    if category.children.exists():
        raise ProblemError(
            ErrorCode.CONFLICT, "Move or delete this category's subcategories first."
        )
    with transaction.atomic():
        audit.record(
            "category.delete", actor=actor, target=category, before=_category_state(category), ip=ip
        )
        pk = category.pk
        category.delete()
        notify_catalog_changed("category", [pk])


def reorder_categories(
    kind: str, ids: Sequence[UUID], *, actor: User | None, ip: str | None
) -> list[Category]:
    """Give the categories of `kind` the order of `ids`; the ones not listed follow in
    their current order."""
    with transaction.atomic():
        categories = list(Category.objects.select_for_update().filter(kind=kind).order_by("sort"))
        by_id = {category.pk: category for category in categories}
        unknown = [str(pk) for pk in ids if pk not in by_id]
        if unknown:
            raise ProblemError(
                ErrorCode.VALIDATION_ERROR,
                f"Not categories of kind {kind}: {', '.join(unknown)}.",
                field_errors={"ids": [f"Unknown or of another kind: {', '.join(unknown)}."]},
            )
        listed = list(dict.fromkeys(ids))
        ordered = [by_id[pk] for pk in listed] + [c for c in categories if c.pk not in set(listed)]
        before = [str(c.pk) for c in categories]
        now = timezone.now()
        for index, category in enumerate(ordered, start=1):
            category.sort = index * _SORT_STEP
            category.updated_at = now
        Category.objects.bulk_update(ordered, ["sort", "updated_at"])
        audit.record(
            "category.reorder",
            actor=actor,
            target=audit.AuditTarget("catalog.category", kind),
            before={"order": before},
            after={"order": [str(c.pk) for c in ordered]},
            ip=ip,
        )
        notify_catalog_changed("category", [c.pk for c in ordered])
    return ordered


def _category_state(category: Category) -> dict[str, Any]:
    return {
        "kind": category.kind,
        "name_en": category.name_en,
        "name_ar": category.name_ar,
        "slug": category.slug,
        "sort": category.sort,
        "is_adult": category.is_adult,
        "visible_in_xtream": category.visible_in_xtream,
        "icon": category.icon,
        "parent": str(category.parent_id) if category.parent_id else None,
    }


# --- Collections (C1, ADR-0013) -------------------------------------------------------------------

COLLECTION_FIELDS: Final = (
    "slug",
    "name_en",
    "name_ar",
    "description_en",
    "description_ar",
    "sort",
    "published",
    "show_on_home",
)
MAX_COLLECTION_ITEMS: Final = 500


def _collection_state(collection: Collection) -> dict[str, Any]:
    return {
        **{name: getattr(collection, name) for name in COLLECTION_FIELDS},
        "items": [
            f"movie:{movie_id}" if movie_id else f"series:{series_id}"
            for movie_id, series_id in collection.items.order_by("sort").values_list(
                "movie_id", "series_id"
            )
        ],
    }


def _set_items(collection: Collection, items: Sequence[Mapping[str, Any]]) -> None:
    """Replace the collection's titles with `items` ([{type, id}], in order)."""
    wanted = list(dict.fromkeys((str(item["type"]), item["id"]) for item in items))
    movie_ids = {pk for kind, pk in wanted if kind == "movie"}
    series_ids = {pk for kind, pk in wanted if kind == "series"}
    known = {
        ("movie", pk) for pk in Movie.objects.filter(pk__in=movie_ids).values_list("pk", flat=True)
    }
    known |= {
        ("series", pk)
        for pk in Series.objects.filter(pk__in=series_ids).values_list("pk", flat=True)
    }
    missing = [f"{kind}:{pk}" for kind, pk in wanted if (kind, pk) not in known]
    if missing:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            "Unknown title.",
            field_errors={
                "items": [
                    field_error(f"Unknown title {item}.", code="does_not_exist") for item in missing
                ]
            },
        )
    collection.items.all().delete()
    CollectionItem.objects.bulk_create(
        [
            CollectionItem(
                collection=collection,
                movie_id=pk if kind == "movie" else None,
                series_id=pk if kind == "series" else None,
                sort=index * _SORT_STEP,
            )
            for index, (kind, pk) in enumerate(wanted, start=1)
        ]
    )


def _collection_changed(collection_id: UUID) -> None:
    from apps.catalog import home  # noqa: PLC0415 (home imports this module)

    home.invalidate_on_commit()
    transaction.on_commit(
        lambda: catalog_changed.send(
            sender=_collection_changed, kind="collection", ids=(collection_id,)
        )
    )


def _check_slug(slug: str, exclude: UUID | None = None) -> None:
    clash = Collection.objects.filter(slug=slug)
    if exclude is not None:
        clash = clash.exclude(pk=exclude)
    if clash.exists():
        raise ProblemError(
            ErrorCode.CONFLICT,
            "Another collection already uses that slug.",
            field_errors={
                "slug": [field_error("Already used by another collection.", code="slug_taken")]
            },
        )


def create_collection(data: Mapping[str, Any], *, actor: User | None, ip: str | None) -> Collection:
    values = {name: data[name] for name in COLLECTION_FIELDS if name in data}
    if not values.get("slug"):
        values["slug"] = slugify(values.get("name_en", ""))[:100] or "collection"
    with transaction.atomic():
        _check_slug(values["slug"])
        collection = Collection.objects.create(**values)
        _set_items(collection, data.get("items") or [])
        audit.record(
            "collection.create",
            actor=actor,
            target=collection,
            after=_collection_state(collection),
            ip=ip,
        )
        _collection_changed(collection.pk)
    return collection


def update_collection(
    collection: Collection, data: Mapping[str, Any], *, actor: User | None, ip: str | None
) -> Collection:
    with transaction.atomic():
        collection = Collection.objects.select_for_update().get(pk=collection.pk)
        before = _collection_state(collection)
        for name in COLLECTION_FIELDS:
            if name in data:
                setattr(collection, name, data[name])
        _check_slug(collection.slug, exclude=collection.pk)
        collection.save()
        if "items" in data:
            _set_items(collection, data["items"] or [])
        after = _collection_state(collection)
        if after != before:
            audit.record(
                "collection.update",
                actor=actor,
                target=collection,
                before=before,
                after=after,
                ip=ip,
            )
            _collection_changed(collection.pk)
    return collection


def delete_collection(collection: Collection, *, actor: User | None, ip: str | None) -> None:
    with transaction.atomic():
        audit.record(
            "collection.delete",
            actor=actor,
            target=collection,
            before=_collection_state(collection),
            ip=ip,
        )
        pk = collection.pk
        collection.delete()
        _collection_changed(pk)
