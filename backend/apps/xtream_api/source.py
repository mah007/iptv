"""The catalog queries the Xtream API needs (SPEC §7.5), behind one protocol.

The Django-model implementation arrives with the catalog models (POC slice 4) and
is installed with `set_catalog_source`; until then the API serves an empty
catalog. Tests install an in-memory fake.

Contract every implementation keeps:
- Only titles the scope may see: allowed categories (all when `scope.categories`
  is None), allowed content types, and playable (status ready) titles.
- `category_ids` hold only categories the same scope's `categories()` returns,
  primary first. The builders drop anything else, and items left without a
  category, so a sloppy source cannot break the contract, only lose items.
- Series are listed only when they have a playable episode.
- `title()` ignores the scope: play URLs resolve the id, then playback runs the
  entitlement checks in SPEC §7.4 order and answers with its own error codes.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from apps.xtream_api.dto import (
    CategoryItem,
    CategoryKind,
    EpisodeRef,
    MovieDetail,
    MovieSummary,
    SeriesDetail,
    SeriesSummary,
    TitleKind,
    TitleRef,
)


@dataclass(frozen=True, slots=True)
class CatalogScope:
    """What an entitlement lets a user see. Locale is not part of it: sources
    return both languages, and responses are cached per scope and locale."""

    categories: frozenset[str] | None = None  # allowed Category primary keys; None = all
    allow_movies: bool = True
    allow_series: bool = True
    allow_live: bool = True

    def fingerprint(self) -> str:
        """A short stable digest; equal scopes share cached responses."""
        categories = "*" if self.categories is None else ",".join(sorted(self.categories))
        flags = f"{int(self.allow_movies)}{int(self.allow_series)}{int(self.allow_live)}"
        return hashlib.sha256(f"{categories}|{flags}".encode()).hexdigest()[:16]


class CatalogSource(Protocol):
    def categories(self, scope: CatalogScope, kind: CategoryKind) -> Sequence[CategoryItem]:
        """Categories of `kind` visible in Xtream that the scope allows."""
        ...

    def movies(self, scope: CatalogScope) -> Sequence[MovieSummary]:
        """Every playable movie the scope may see."""
        ...

    def movie(self, scope: CatalogScope, xc_id: int) -> MovieDetail | None:
        """One playable movie the scope may see, or None."""
        ...

    def series_list(self, scope: CatalogScope) -> Sequence[SeriesSummary]:
        """Every series with a playable episode that the scope may see."""
        ...

    def series(self, scope: CatalogScope, xc_id: int) -> SeriesDetail | None:
        """One series the scope may see, with its seasons and playable episodes."""
        ...

    def episodes(self, scope: CatalogScope) -> Sequence[EpisodeRef]:
        """Every playable episode of the series `series_list` returns (for M3U)."""
        ...

    def title(self, kind: TitleKind, xc_id: int) -> TitleRef | None:
        """The movie or episode behind a play URL, whatever the scope; None if unknown."""
        ...


class EmptyCatalogSource:
    """The catalog before the catalog models are wired in: valid, and empty."""

    def categories(self, scope: CatalogScope, kind: CategoryKind) -> Sequence[CategoryItem]:
        return ()

    def movies(self, scope: CatalogScope) -> Sequence[MovieSummary]:
        return ()

    def movie(self, scope: CatalogScope, xc_id: int) -> MovieDetail | None:
        return None

    def series_list(self, scope: CatalogScope) -> Sequence[SeriesSummary]:
        return ()

    def series(self, scope: CatalogScope, xc_id: int) -> SeriesDetail | None:
        return None

    def episodes(self, scope: CatalogScope) -> Sequence[EpisodeRef]:
        return ()

    def title(self, kind: TitleKind, xc_id: int) -> TitleRef | None:
        return None


_source: CatalogSource = EmptyCatalogSource()


def catalog_source() -> CatalogSource:
    return _source


def set_catalog_source(source: CatalogSource) -> CatalogSource:
    """Install `source` (at startup, or in tests); returns the previous one."""
    global _source
    previous, _source = _source, source
    return previous
