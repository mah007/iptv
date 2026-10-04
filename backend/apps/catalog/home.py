"""The shared part of the home screen (SPEC §9 Home), cached in redis-cache.

Rows that depend only on what a customer may browse and on the language (the hero,
recently added, popular this week, collections, one row per category) are built once
per entitlement scope and locale, serialized, and stored under

    home:<version>:<scope fingerprint>:<locale>

for `TTL_S`. Any catalogue change (`catalog_changed`, connected in `CatalogConfig.ready`)
replaces `home:version`, which retires every entry at once, as the Xtream cache does.
Personal rows (continue watching, recommendations) are added per request on top.
redis-cache holds recomputable data only: when it is down, rows are built directly.
"""

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any, Final, cast
from uuid import UUID

import orjson
import redis
from django.db import transaction

from apps.catalog.browse import CustomerScope, visible_categories, visible_movies, visible_series
from apps.catalog.i18n import Locale
from apps.catalog.models import Collection, CollectionItem, Movie, Series
from apps.catalog.queries import Title, TitleRef, hydrate
from apps.catalog.serializers_customer import (
    CategorySerializer,
    HeroSerializer,
    TitleCardSerializer,
)
from apps.core.ids import uuid7
from apps.core.stores import cache_redis

logger = logging.getLogger(__name__)

PREFIX: Final = "home"
VERSION_KEY: Final = f"{PREFIX}:version"
TTL_S: Final = 600
ROW_SIZE: Final = 20
HERO_SIZE: Final = 8
MAX_CATEGORY_ROWS: Final = 12
MAX_COLLECTION_ROWS: Final = 10

#: Display names of the built-in rows; data-driven rows carry their own names.
ROW_TITLES: Final[dict[str, dict[str, str]]] = {
    "continue_watching": {"en": "Continue watching", "ar": "تابع المشاهدة"},
    "recently_added": {"en": "Recently added", "ar": "أضيف حديثًا"},
    "popular": {"en": "Popular this week", "ar": "الأكثر مشاهدة هذا الأسبوع"},
    "top_picks": {"en": "Top picks for you", "ar": "مختارات لك"},
    "because_you_watched": {"en": "Because you watched {title}", "ar": "لأنك شاهدت {title}"},
    "my_list": {"en": "My list", "ar": "قائمتي"},
}


def row_title(kind: str, locale: Locale, **values: str) -> str:
    return ROW_TITLES[kind][locale].format(**values)


# --- Cache ------------------------------------------------------------------------------------


def _version(client: redis.Redis) -> str:
    raw = cast("bytes | None", client.get(VERSION_KEY))
    if raw is None:
        client.set(VERSION_KEY, uuid7().hex, nx=True)
        raw = cast("bytes | None", client.get(VERSION_KEY))
    return raw.decode() if raw is not None else ""


def invalidate() -> None:
    try:
        cache_redis().set(VERSION_KEY, uuid7().hex)
    except redis.RedisError:
        logger.warning("home cache unavailable; entries expire with their TTL")


def invalidate_on_commit(**_kwargs: Any) -> None:
    """`catalog_changed` receiver (also called after collection edits)."""
    transaction.on_commit(invalidate, robust=True)


def cached(
    scope: CustomerScope, locale: Locale, build: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    client = cache_redis()
    key = ""
    try:
        key = f"{PREFIX}:{_version(client)}:{scope.fingerprint()}:{locale}"
        hit = cast("bytes | None", client.get(key))
    except redis.RedisError:
        logger.warning("home cache unavailable; building rows without it")
        hit = None
    if hit is not None:
        return cast("dict[str, Any]", orjson.loads(hit))
    payload = build()
    if key:
        try:
            client.set(key, orjson.dumps(payload, default=str), ex=TTL_S)
        except redis.RedisError:
            logger.warning("home cache unavailable; rows not stored")
    return payload


# --- The rows ---------------------------------------------------------------------------------


def _cards(titles: list[Title], locale: Locale) -> list[dict[str, Any]]:
    data = TitleCardSerializer(titles, many=True, context={"locale": locale}).data
    return cast("list[dict[str, Any]]", orjson.loads(orjson.dumps(data, default=str)))


def _hero(scope: CustomerScope, locale: Locale) -> list[dict[str, Any]]:
    refs: list[TitleRef] = [
        ("movie", pk)
        for pk in visible_movies(scope).filter(featured=True).values_list("pk", flat=True)
    ]
    refs += [
        ("series", pk)
        for pk in visible_series(scope).filter(featured=True).values_list("pk", flat=True)
    ]
    titles = [title for title in hydrate(scope, refs) if _has_backdrop(title)]
    if len(titles) < HERO_SIZE:
        seen = {(type(title), title.pk) for title in titles}
        extra = [
            title
            for title in _by(scope, "-popularity", HERO_SIZE * 3)
            if _has_backdrop(title) and (type(title), title.pk) not in seen
        ]
        titles += extra[: HERO_SIZE - len(titles)]
    data = HeroSerializer(titles[:HERO_SIZE], many=True, context={"locale": locale}).data
    return cast("list[dict[str, Any]]", orjson.loads(orjson.dumps(data, default=str)))


def _has_backdrop(title: Title) -> bool:
    return any(image.kind == "backdrop" for image in getattr(title, "art", []))


def _by(scope: CustomerScope, order: str, limit: int) -> list[Title]:
    """Movies and series together, ordered by one field (descending with `-`)."""
    field = order.lstrip("-")
    rows: list[tuple[Any, TitleRef]] = [
        (value, ("movie", pk))
        for pk, value in visible_movies(scope)
        .order_by(order, "pk")
        .values_list("pk", field)[:limit]
    ]
    rows += [
        (value, ("series", pk))
        for pk, value in visible_series(scope)
        .order_by(order, "pk")
        .values_list("pk", field)[:limit]
    ]
    rows.sort(key=lambda row: row[0], reverse=order.startswith("-"))
    return hydrate(scope, [ref for _value, ref in rows[:limit]])


def _category_rows(scope: CustomerScope, locale: Locale) -> list[dict[str, Any]]:
    categories = list(visible_categories(scope).order_by("kind", "sort", "name_en"))
    by_id = {category.pk: category for category in categories}
    members: dict[UUID, list[TitleRef]] = defaultdict(list)
    movie_links = (
        Movie.categories.through.objects.filter(
            category_id__in=list(by_id), movie__in=visible_movies(scope)
        )
        .order_by("-movie__popularity", "movie_id")
        .values_list("category_id", "movie_id")
    )
    for category_id, movie_id in movie_links:
        if len(members[category_id]) < ROW_SIZE:
            members[category_id].append(("movie", movie_id))
    series_links = (
        Series.categories.through.objects.filter(
            category_id__in=list(by_id), series__in=visible_series(scope)
        )
        .order_by("-series__popularity", "series_id")
        .values_list("category_id", "series_id")
    )
    for category_id, series_id in series_links:
        if len(members[category_id]) < ROW_SIZE:
            members[category_id].append(("series", series_id))
    chosen = [category for category in categories if members.get(category.pk)][:MAX_CATEGORY_ROWS]
    titles = {
        ("movie" if isinstance(title, Movie) else "series", title.pk): title
        for title in hydrate(scope, [ref for c in chosen for ref in members[c.pk]])
    }
    rows = []
    for category in chosen:
        items = [titles[ref] for ref in members[category.pk] if ref in titles]
        if not items:
            continue
        meta = CategorySerializer(category, context={"locale": locale}).data
        rows.append(
            {
                "kind": "category",
                "key": f"category:{category.slug}",
                "title": meta["name"],
                "category": orjson.loads(orjson.dumps(meta, default=str)),
                "items": _cards(items, locale),
            }
        )
    return rows


def _collection_rows(scope: CustomerScope, locale: Locale) -> list[dict[str, Any]]:
    collections = list(
        Collection.objects.filter(published=True, show_on_home=True).order_by("sort", "name_en")[
            :MAX_COLLECTION_ROWS
        ]
    )
    if not collections:
        return []
    refs: dict[UUID, list[TitleRef]] = defaultdict(list)
    for collection_id, movie_id, series_id in (
        CollectionItem.objects.filter(collection__in=collections)
        .order_by("sort", "created_at")
        .values_list("collection_id", "movie_id", "series_id")
    ):
        refs[collection_id].append(("movie", movie_id) if movie_id else ("series", series_id))
    titles = {
        ("movie" if isinstance(title, Movie) else "series", title.pk): title
        for title in hydrate(scope, [ref for items in refs.values() for ref in items])
    }
    rows = []
    for collection in collections:
        items = [titles[ref] for ref in refs.get(collection.pk, []) if ref in titles][:ROW_SIZE]
        if items:
            rows.append(
                {
                    "kind": "collection",
                    "key": f"collection:{collection.slug}",
                    "title": (collection.name_ar or collection.name_en)
                    if locale == "ar"
                    else collection.name_en,
                    "collection_slug": collection.slug,
                    "items": _cards(items, locale),
                }
            )
    return rows


def shared_rows(scope: CustomerScope, locale: Locale) -> dict[str, Any]:
    """The cached, non-personal home content for this scope and language."""

    def build() -> dict[str, Any]:
        from apps.engagement.recommend import popular  # noqa: PLC0415 (engagement imports catalog)

        rows: list[dict[str, Any]] = []
        recent = _by(scope, "-created_at", ROW_SIZE)
        if recent:
            rows.append(
                {
                    "kind": "recently_added",
                    "key": "recently_added",
                    "title": row_title("recently_added", locale),
                    "items": _cards(recent, locale),
                }
            )
        trending = popular(scope, ROW_SIZE)
        if trending:
            rows.append(
                {
                    "kind": "popular",
                    "key": "popular",
                    "title": row_title("popular", locale),
                    "items": _cards(trending, locale),
                }
            )
        rows += _collection_rows(scope, locale)
        rows += _category_rows(scope, locale)
        return {"hero": _hero(scope, locale), "rows": rows}

    return cached(scope, locale, build)
