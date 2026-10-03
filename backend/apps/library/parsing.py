"""Turn a library-relative media path into a typed `ParseResult` (SPEC §7.1, §7.2 step 1).

guessit (LGPL-3.0, used unmodified as a library) parses the full relative path,
folders included. Its result is then corrected with the conventions that Plex,
Jellyfin, scene/fansub releases and Arabic libraries use:

- `Movie (1999)/…` names the movie and its year; `Matrix, The (1999)` is
  read as "The Matrix";
- `Show (2008)/Season 01/…`, `Show/S2/…`, `Show/الموسم 2/…`, `Show/الجزء 2/…`
  and `Show/Specials/…` (season 0) name the series and the season;
- `[tmdbid-603]`, `{tmdb-603}`, `[imdbid-tt0133093]`, `{imdb-tt…}`,
  `[tvdbid-81189]` and `{tvdb-…}` tags carry provider IDs (the closest one to
  the file wins) and `{edition-…}` an edition, taken verbatim;
- `[Group] Title - 05 [1080p]` is anime-style numbering: absolute unless a
  season folder says otherwise;
- Arabic markers: `الحلقة 5` (episode), `الموسم 2` (season), `الجزء 2` next to
  an episode marker (season), `مدبلج` (dubbed: Arabic audio), `مترجم`
  (subtitled: Arabic subtitles), and `مسلسل` (series) or `فيلم` (film) in
  front of a title. Arabic-Indic digits are read as numbers and kept as written
  in an Arabic title.

Paths that are not media (other extensions, hidden and NAS system files),
samples, trailers and extras parse to `kind="ignore"` with a reason. Nothing
here touches the file system or the database. The expected behaviour is pinned
by `backend/tests/data/filenames.csv`.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from functools import lru_cache
from pathlib import PurePath, PurePosixPath
from types import MappingProxyType
from typing import Any, Final, Literal, cast

from guessit import guessit

from apps.search.normalize import normalize

__all__ = [
    "VIDEO_EXTENSIONS",
    "IgnoreReason",
    "LibraryKind",
    "MediaKind",
    "ParseResult",
    "ProviderIds",
    "parse_path",
]

type MediaKind = Literal["movie", "episode", "ignore"]
type LibraryKind = Literal["movies", "series", "documentaries", "kids", "mixed"]
type IgnoreReason = Literal["extension", "hidden", "sample", "extra"]

#: SPEC §7.1: only these containers are scanned.
VIDEO_EXTENSIONS: Final = frozenset({"mkv", "mp4", "m4v", "mov", "avi", "ts", "m2ts", "webm"})

# --- ignore rules ----------------------------------------------------------------

_SAMPLE_DIRS: Final = frozenset({"sample", "samples"})
_EXTRA_DIRS: Final = frozenset(
    {
        "trailer", "trailers", "extra", "extras", "featurette", "featurettes",
        "behind the scenes", "behindthescenes", "deleted scenes", "deletedscenes",
        "interview", "interviews", "scene", "scenes", "short", "shorts",
        "bonus", "bonus features", "making of",
    }
)  # fmt: skip
_SYSTEM_DIRS: Final = frozenset(
    {"@eadir", "#recycle", "$recycle.bin", "lost+found", "#snapshot", "system volume information"}
)
_SAMPLE_STEM: Final = re.compile(r"^sample$|^sample[-._ ]|[-._ ]sample$", re.IGNORECASE)
_EXTRA_STEM: Final = re.compile(
    r"^(?:trailer|teaser)$|[-._ ](?:trailer|teaser)$"
    r"|-(?:behindthescenes|deleted|deletedscene|featurette|interview|scene|short|other|extra|clip)$",
    re.IGNORECASE,
)

# --- tags and markers --------------------------------------------------------------

_PROVIDER_TAG: Final = re.compile(
    r"[\[{(]\s*(?P<provider>tmdbid|tmdb|imdbid|imdb|tvdbid|tvdb)\s*[-=:]\s*"
    r"(?P<value>tt\d{5,10}|\d{1,10})\s*[\]})]",
    re.IGNORECASE,
)
_EDITION_TAG: Final = re.compile(r"\{edition-(?P<value>[^}]+)\}", re.IGNORECASE)
_SORT_ARTICLE: Final = re.compile(
    r"^(?P<rest>[^,]+?),\s*(?P<article>the|a|an)\b(?=\s*(?:[(\[{]|\.[a-z0-9]{2,4}$|$))",
    re.IGNORECASE,
)
_COUNTRY_SUFFIX: Final = re.compile(r"\s*\((?:US|UK|AU|NZ|CA|IE)\)\s*$")

_ARABIC_INDIC: Final = "٠١٢٣٤٥٦٧٨٩"
_EASTERN_ARABIC_INDIC: Final = "۰۱۲۳۴۵۶۷۸۹"
_TO_ASCII_DIGITS: Final = str.maketrans(
    {c: str(d) for d, c in enumerate(_ARABIC_INDIC)}
    | {c: str(d) for d, c in enumerate(_EASTERN_ARABIC_INDIC)}
)
_ARABIC_LETTERS: Final = re.compile(r"[ء-يٱ-ۓ]")

_AR_EPISODE: Final = re.compile(r"(?:ال)?حلق[ةه]\s*[-_.:#]?\s*(\d{1,4})")
_AR_SEASON: Final = re.compile(r"(?:ال)?موسم\s*[-_.:#]?\s*(\d{1,4})")
_AR_PART: Final = re.compile(r"(?:ال)?جزء\s*[-_.:#]?\s*(\d{1,4})")
_AR_BOUNDARY_BEFORE: Final = r"(?:^|(?<=[\s._\-\[\](){}]))"
_AR_BOUNDARY_AFTER: Final = r"(?=$|[\s._\-\[\](){}])"
_AR_DUBBED: Final = re.compile(f"{_AR_BOUNDARY_BEFORE}مدبلج[ةه]?{_AR_BOUNDARY_AFTER}")
_AR_SUBTITLED: Final = re.compile(f"{_AR_BOUNDARY_BEFORE}مترجم[ةه]?{_AR_BOUNDARY_AFTER}")
_AR_TITLE_PREFIX: Final = re.compile(r"^(?:مسلسل|فيلم)\s+")
# "ARABIC", "Arabic.Dubbed", "ARA.Subs" in the release part of a name (after the
# year or an SxxEyy marker): guessit does not know these spellings and would
# otherwise read "St.ar" in "Star Wars" as Arabic subtitles.
_RELEASE_AREA: Final = re.compile(
    r"(?<![a-z0-9])(?:(?:19|20)\d{2}|s\d{1,4}e\d{1,4}|\d{3,4}p)(?![a-z0-9])", re.IGNORECASE
)
_ARABIC_TAG: Final = re.compile(
    r"(?<![a-z0-9])(?:arabic|ara)(?:[ ._-]?(?P<sub>sub(?:s|bed|titles?)?)|[ ._-]?dub(?:bed)?)?"
    r"(?![a-z0-9])",
    re.IGNORECASE,
)

_SEASON_DIR: Final = re.compile(
    r"^(?:season|series|saison|staffel|temporada|stagione|seizoen|sezon|sezona|s)"
    r"[ ._-]*(\d{1,4})(?!\d)",
    re.IGNORECASE,
)
_SPECIALS_DIR: Final = re.compile(r"^(?:specials?|season[ ._-]*specials?)$", re.IGNORECASE)
# Matched against normalised folder names, where "الموسم" is already "موسم".
_AR_SEASON_DIR: Final = re.compile(r"^(?:موسم|جزء)\s*(\d{1,4})$")
_AR_ORDINAL_SEASON_DIR: Final = re.compile(r"^(?:موسم|جزء)\s+(\S+)$")
_AR_ORDINALS: Final[Mapping[str, int]] = {
    "اول": 1, "ثاني": 2, "ثالث": 3, "رابع": 4, "خامس": 5,
    "سادس": 6, "سابع": 7, "ثامن": 8, "تاسع": 9, "عاشر": 10,
}  # fmt: skip

# A season written in the name itself (S01, 1x02, "Season 2"), as opposed to
# guessit turning a bare "101" into S01E01 or a series year into a season.
_EXPLICIT_SEASON: Final = re.compile(
    r"(?:^|[^a-z0-9])(?:s\d{1,4}(?=e\d|[^a-z0-9]|$)|\d{1,2}x\d{1,4}(?!\d)"
    r"|season|saison|staffel|temporada)",
    re.IGNORECASE,
)
_EPISODE_MARKER: Final = re.compile(
    r"(?:^|[^a-z0-9])"
    r"(?:s\d{1,4}[ ._-]?e\d|\d{1,2}x\d{1,4}(?!\d)|e\d{1,4}(?!\d)|ep(?:isode)?[ ._-]*\d)",
    re.IGNORECASE,
)
_DATE: Final = re.compile(
    r"(?<!\d)(?:(?:19|20)\d{2}[ ._-]\d{2}[ ._-]\d{2}|\d{2}[ ._-]\d{2}[ ._-](?:19|20)\d{2})(?!\d)"
)
# "[Group] Title - 05 [1080p]", "Title - 1071", "Show (2011) - 148v2"
_ABSOLUTE: Final = re.compile(
    r"^(?:\[[^\]]+\]\s*)?(?P<title>.+?)\s+-\s+(?P<episode>\d{1,4})(?:v\d{1,2})?"
    r"(?=\s*(?:[\[(]|-\s|$))"
)
_TITLE_YEAR: Final = re.compile(r"^(?P<title>.+?)\s*\((?P<year>(?:18|19|20)\d{2})\)")
_TITLE_PART: Final = re.compile(
    r"(?<![a-z0-9])(?P<word>part|pt|vol|volume)[ ._-]*"
    r"(?P<number>\d{1,2}|[ivx]{1,4}|one|two|three|four|five|six|seven|eight|nine|ten)(?![a-z0-9])",
    re.IGNORECASE,
)
_RELEASE_JUNK: Final = re.compile(
    r"(?<![a-z0-9])(?:\d{3,4}p|bluray|blu-ray|web-?dl|webrip|hdtv|dvdrip|x26[45]|h\.?26[45]|hevc"
    r"|s\d{1,2}(?:e\d{1,3})?)(?![a-z0-9])",
    re.IGNORECASE,
)
_EDITION_WORDS: Final = re.compile(
    r"\b(?:cut|edition|version|remaster(?:ed)?|redux|uncut|unrated)\b", re.IGNORECASE
)
_LANGUAGE_WORDS: Final = frozenset(
    {
        "arabic", "ara", "english", "eng", "french", "dubbed", "subbed", "subs",
        "multi", "dual", "audio", "عربي", "مدبلج", "مترجم",
    }
)  # fmt: skip

# guessit edition values -> the canonical labels the catalog shows.
_EDITION_LABELS: Final[Mapping[str, str]] = {
    "collector": "Collector's Edition",
    "special": "Special Edition",
    "ultimate": "Ultimate Edition",
    "the final cut": "Final Cut",
    "final": "Final Cut",
}


@dataclass(frozen=True, slots=True)
class ProviderIds:
    """IDs of the movie or series a file belongs to, read from the path."""

    tmdb: int | None = None
    imdb: str | None = None
    tvdb: int | None = None

    def __bool__(self) -> bool:
        return self.tmdb is not None or self.imdb is not None or self.tvdb is not None


@dataclass(frozen=True, slots=True)
class ParseResult:
    """What the path says about the media file. Unknown values are None or empty.

    - `title` is the movie or series title; `year` the movie year or the
      series' first-air year (`Show (2008)/…`, `Doctor.Who.2005.S01E01`).
    - Episodes use `season` + `episodes` (`S01E01E02` gives `(1, 2)`); specials
      are season 0. Absolute numbering (`Show - 1071`) leaves `season` None and
      `episodes` empty and sets `absolute_episode`; dated episodes set `air_date`.
    - `edition` is a canonical label ("Director's Cut", "Extended, Remastered")
      or the verbatim value of a `{edition-…}` tag. `part` is a multi-part
      movie file (`CD1`, `- Part 1` after the year), not a sequel's "Part Two".
    - `ambiguous` means the library kind and the name disagree (`S01E01` in a
      movies library) or an episode has no number: send it to review.
    - `provider_ids` belong to the movie or the series, never to an episode.
    """

    kind: MediaKind
    title: str | None = None
    year: int | None = None
    alternative_title: str | None = None
    season: int | None = None
    episodes: tuple[int, ...] = ()
    absolute_episode: int | None = None
    air_date: date | None = None
    episode_title: str | None = None
    edition: str | None = None
    part: int | None = None
    resolution: str | None = None
    source: str | None = None
    codec: str | None = None
    languages: tuple[str, ...] = ()
    subtitle_languages: tuple[str, ...] = ()
    provider_ids: ProviderIds = field(default_factory=ProviderIds)
    container: str | None = None
    ambiguous: bool = False
    ignore_reason: IgnoreReason | None = None

    def to_json(self) -> dict[str, Any]:
        """A JSON-safe dict, for `MediaFile.parse_result` and `MatchReview.parse_result`."""
        return {
            "kind": self.kind,
            "title": self.title,
            "year": self.year,
            "alternative_title": self.alternative_title,
            "season": self.season,
            "episodes": list(self.episodes),
            "absolute_episode": self.absolute_episode,
            "air_date": self.air_date.isoformat() if self.air_date else None,
            "episode_title": self.episode_title,
            "edition": self.edition,
            "part": self.part,
            "resolution": self.resolution,
            "source": self.source,
            "codec": self.codec,
            "languages": list(self.languages),
            "subtitle_languages": list(self.subtitle_languages),
            "provider_ids": {
                "tmdb": self.provider_ids.tmdb,
                "imdb": self.provider_ids.imdb,
                "tvdb": self.provider_ids.tvdb,
            },
            "container": self.container,
            "ambiguous": self.ambiguous,
            "ignore_reason": self.ignore_reason,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "ParseResult":
        """Inverse of `to_json`; unknown keys are ignored so old rows keep loading."""
        ids = data.get("provider_ids") or {}
        air_date = data.get("air_date")
        return cls(
            kind=data["kind"],
            title=data.get("title"),
            year=data.get("year"),
            alternative_title=data.get("alternative_title"),
            season=data.get("season"),
            episodes=tuple(data.get("episodes") or ()),
            absolute_episode=data.get("absolute_episode"),
            air_date=date.fromisoformat(air_date) if air_date else None,
            episode_title=data.get("episode_title"),
            edition=data.get("edition"),
            part=data.get("part"),
            resolution=data.get("resolution"),
            source=data.get("source"),
            codec=data.get("codec"),
            languages=tuple(data.get("languages") or ()),
            subtitle_languages=tuple(data.get("subtitle_languages") or ()),
            provider_ids=ProviderIds(
                tmdb=ids.get("tmdb"), imdb=ids.get("imdb"), tvdb=ids.get("tvdb")
            ),
            container=data.get("container"),
            ambiguous=bool(data.get("ambiguous", False)),
            ignore_reason=data.get("ignore_reason"),
        )


@dataclass(slots=True)
class _Tags:
    """What `_clean_segment` takes out of the path before guessit sees it."""

    languages: list[str] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)
    edition: str | None = None
    arabic_episodes: tuple[int, ...] = ()
    digits: str | None = None  # the Arabic digit family the name was written in


@dataclass(frozen=True, slots=True)
class _FolderHints:
    season: int | None = None
    show_title: str | None = None
    show_year: int | None = None
    movie_title: str | None = None
    movie_year: int | None = None
    parent_title: str | None = None


def parse_path(path: str | PurePath, *, library_kind: LibraryKind = "mixed") -> ParseResult:
    """Parse a library-relative path such as `Breaking Bad/Season 01/S01E01.mkv`.

    `library_kind` comes from the library: `movies` makes every file a movie and
    `series` every file an episode (flagging names that disagree as ambiguous);
    the other kinds let the name decide.
    """
    parts = _split(path)
    if not parts:
        return ParseResult(kind="ignore", ignore_reason="extension")
    *dirs, name = parts
    stem, _, extension = name.rpartition(".")
    extension = extension.lower()
    if not stem or extension not in VIDEO_EXTENSIONS:
        return ParseResult(kind="ignore", ignore_reason="extension", container=extension or None)
    reason = _ignore_reason(dirs, stem)
    if reason is not None:
        return ParseResult(kind="ignore", ignore_reason=reason, container=extension)

    tags = _Tags()
    cleaned_dirs = [_clean_segment(part, tags, is_name=False) for part in dirs]
    cleaned_stem = _clean_segment(stem, tags, is_name=True)
    hints = _folder_hints(cleaned_dirs)
    full = "/".join([*cleaned_dirs, f"{cleaned_stem}.{extension}"])

    guess = _guess(full, None)
    flagged = _flagged_extra(guess, cleaned_stem)
    if flagged is not None:
        return ParseResult(kind="ignore", ignore_reason=flagged, container=extension)

    explicit_in_name = bool(_EXPLICIT_SEASON.search(cleaned_stem))
    explicit_in_path = explicit_in_name or any(_EXPLICIT_SEASON.search(d) for d in cleaned_dirs)
    dated = guess.get("date") is not None or bool(_DATE.search(cleaned_stem))
    numbered = guess.get("episode") is not None or guess.get("season") is not None

    kind: MediaKind
    ambiguous = False
    if library_kind == "movies":
        kind = "movie"
        ambiguous = guess.get("type") == "episode" and bool(_EPISODE_MARKER.search(cleaned_stem))
    elif library_kind == "series":
        kind = "episode"
    else:
        is_episode = numbered or hints.season is not None or bool(tags.arabic_episodes)
        kind = "episode" if is_episode or dated else "movie"

    if kind == "movie":
        if guess.get("type") != "movie":
            guess = _guess(full, "movie")
        result = _movie_result(guess, hints, cleaned_stem, tags)
    else:
        if guess.get("type") != "episode" or not _has_episode_or_date(guess):
            guess = _guess(full, "episode")
        if guess.get("season") is not None and not explicit_in_path and hints.season is None:
            # guessit reads a bare "101" as S01E01; without any season marker it
            # is far more often anime-style absolute numbering.
            guess = _guess(full, "episode", episode_prefer_number=True)
        result = _episode_result(
            guess,
            hints,
            cleaned_stem,
            tags,
            explicit_in_name=explicit_in_name,
            explicit_in_path=explicit_in_path,
        )
        no_number = not result.episodes and result.absolute_episode is None
        ambiguous = no_number and result.air_date is None and result.season != 0

    title = _restore_digits(result.title, tags.digits)
    return replace(
        result,
        title=title,
        ambiguous=ambiguous,
        resolution=_text(guess.get("screen_size")),
        source=_first_text(guess.get("source")),
        codec=_first_text(guess.get("video_codec")),
        languages=_merge_languages(tags.languages, guess.get("language")),
        subtitle_languages=_merge_languages(
            tags.subtitle_languages, guess.get("subtitle_language")
        ),
        provider_ids=_merge_ids(_provider_ids(parts), guess),
        container=extension,
    )


# --- steps ---------------------------------------------------------------------------


def _split(path: str | PurePath) -> list[str]:
    text = str(path).replace("\\", "/")
    return [part for part in PurePosixPath(text).parts if part not in {"/", ".", ""}]


def _ignore_reason(dirs: Iterable[str], stem: str) -> IgnoreReason | None:
    for part in (*dirs, stem):
        if part.startswith(".") or part.casefold() in _SYSTEM_DIRS:
            return "hidden"
    for directory in dirs:
        folded = directory.casefold().strip()
        if folded in _SAMPLE_DIRS:
            return "sample"
        if folded in _EXTRA_DIRS:
            return "extra"
    if _SAMPLE_STEM.search(stem):
        return "sample"
    if _EXTRA_STEM.search(stem):
        return "extra"
    return None


def _flagged_extra(guess: Mapping[str, object], stem: str) -> IgnoreReason | None:
    """`Arrival.2016.SAMPLE.mkv`, `Oppenheimer.2023.Trailer.mp4`, but not `Trailer.Park.Boys`.

    guessit flags the word anywhere; only a tag in the release part of the name
    (from the year or SxxEyy marker on) marks a sample or a trailer.
    """
    other = guess.get("other")
    flags = set(other) if isinstance(other, list) else {other}
    area = _RELEASE_AREA.search(stem)
    release = stem[area.start() :].casefold() if area else ""
    if "Sample" in flags and re.search(r"(?<![a-z0-9])sample(?![a-z0-9])", release):
        return "sample"
    if "Trailer" in flags and re.search(r"(?<![a-z0-9])(?:trailer|teaser)(?![a-z0-9])", release):
        return "extra"
    return None


def _provider_ids(parts: list[str]) -> ProviderIds:
    """Provider tags anywhere in the path; the one closest to the file wins."""
    found: dict[str, int | str] = {}
    for part in reversed(parts):
        for match in _PROVIDER_TAG.finditer(part):
            provider = match["provider"].lower().removesuffix("id")
            value = match["value"].lower()
            if provider in found:
                continue
            if provider == "imdb":
                found[provider] = value if value.startswith("tt") else f"tt{value}"
            elif value.isdigit():
                found[provider] = int(value)
    tmdb, imdb, tvdb = found.get("tmdb"), found.get("imdb"), found.get("tvdb")
    return ProviderIds(
        tmdb=tmdb if isinstance(tmdb, int) else None,
        imdb=imdb if isinstance(imdb, str) else None,
        tvdb=tvdb if isinstance(tvdb, int) else None,
    )


def _clean_segment(part: str, tags: _Tags, *, is_name: bool) -> str:
    """Take tags and markers guessit misreads out of one path segment."""
    text = _PROVIDER_TAG.sub(" ", part)
    edition = _EDITION_TAG.search(text)
    if edition:
        if is_name or tags.edition is None:
            tags.edition = edition["value"].strip()
        text = _EDITION_TAG.sub(" ", text)
    if tags.digits is None:
        if any(char in _ARABIC_INDIC for char in text):
            tags.digits = _ARABIC_INDIC
        elif any(char in _EASTERN_ARABIC_INDIC for char in text):
            tags.digits = _EASTERN_ARABIC_INDIC
    text = _SORT_ARTICLE.sub(r"\g<article> \g<rest>", text.translate(_TO_ASCII_DIGITS))
    if _AR_DUBBED.search(text):
        tags.languages.append("ar")
        text = _AR_DUBBED.sub(" ", text)
    if _AR_SUBTITLED.search(text):
        tags.subtitle_languages.append("ar")
        text = _AR_SUBTITLED.sub(" ", text)
    text = _strip_arabic_tag(text, tags)
    episodes = tuple(int(number) for number in _AR_EPISODE.findall(text))
    if episodes:
        if is_name:
            tags.arabic_episodes = episodes
        # "الجزء 2" next to an episode marker numbers the season.
        text = _AR_PART.sub(r" Season \1 ", text)
    text = _AR_EPISODE.sub(r" Episode \1 ", text)
    text = _AR_SEASON.sub(r" Season \1 ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _strip_arabic_tag(text: str, tags: _Tags) -> str:
    area = _RELEASE_AREA.search(text)
    if area is None:
        return text
    head, tail = text[: area.end()], text[area.end() :]
    for match in _ARABIC_TAG.finditer(tail):
        if match["sub"]:
            tags.subtitle_languages.append("ar")
        else:
            tags.languages.append("ar")
    return head + _ARABIC_TAG.sub(" ", tail)


def _folder_hints(dirs: list[str]) -> _FolderHints:
    season: int | None = None
    show_title = show_year = None
    for index in range(len(dirs) - 1, -1, -1):
        number = _season_number(dirs[index])
        if number is not None:
            season = number
            if index > 0:
                show_title, show_year = _folder_title(dirs[index - 1])
            break
    movie_title = movie_year = parent_title = None
    if dirs:
        parent = dirs[-1]
        movie = _TITLE_YEAR.match(parent)
        if movie:
            movie_title = _clean_title(movie["title"].strip(" .-_"))
            movie_year = int(movie["year"])
        parent_title = _folder_title(parent)[0]
    return _FolderHints(
        season=season,
        show_title=show_title,
        show_year=show_year,
        movie_title=movie_title,
        movie_year=movie_year,
        parent_title=parent_title,
    )


def _folder_title(name: str) -> tuple[str | None, int | None]:
    """A clean `Show (2008)` folder is taken as written; release names go to guessit."""
    if " " in name and not _RELEASE_JUNK.search(name):
        match = _TITLE_YEAR.match(name)
        title = match["title"] if match else name
        year = int(match["year"]) if match else None
        return _clean_title(_COUNTRY_SUFFIX.sub("", title).strip(" .-_")), year
    guess = _guess_folder(name, None)
    return _clean_title(_first_text(guess.get("title"))), _int(guess.get("year"))


def _season_number(directory: str) -> int | None:
    text = directory.strip()
    if _SPECIALS_DIR.match(text):
        return 0
    match = _SEASON_DIR.match(text)
    if match:
        return int(match[1])
    folded = normalize(text)
    match = _AR_SEASON_DIR.match(folded)
    if match:
        return int(match[1])
    match = _AR_ORDINAL_SEASON_DIR.match(folded)
    if match and match[1] in _AR_ORDINALS:
        return _AR_ORDINALS[match[1]]
    return None


def _movie_result(
    guess: Mapping[str, object], hints: _FolderHints, stem: str, tags: _Tags
) -> ParseResult:
    title = _clean_title(_first_text(guess.get("title")))
    year = _int(guess.get("year"))
    part = _int(guess.get("cd")) or _int(guess.get("part"))
    sequel = _title_part(stem, year)
    if title and sequel:
        # "Dune.Part.Two.2024": the part belongs to the title, it is not a CD.
        title, part = f"{title} {sequel}", None
    if hints.movie_title and (
        year is None
        or year == hints.movie_year
        or normalize(stem).startswith(normalize(hints.movie_title))
    ):
        # `Title (Year)/` is the Plex/Jellyfin convention for the canonical name.
        title, year = hints.movie_title, hints.movie_year
    elif title and hints.parent_title and normalize(title) == normalize(hints.parent_title):
        title = hints.parent_title
    alternative = _clean_title(_first_text(guess.get("alternative_title")))
    if alternative and _is_language_word(alternative):
        alternative = None
    edition = tags.edition or _edition(guess.get("edition"), stem)
    if alternative and not edition and _EDITION_WORDS.search(alternative):
        # "Blade Runner (1982) - Final Cut": guessit only knows the common editions.
        edition, alternative = _edition(alternative, stem), None
    return ParseResult(
        kind="movie",
        title=title,
        year=year,
        alternative_title=alternative,
        edition=edition,
        part=part,
    )


def _episode_result(  # noqa: PLR0913
    guess: Mapping[str, object],
    hints: _FolderHints,
    stem: str,
    tags: _Tags,
    *,
    explicit_in_name: bool,
    explicit_in_path: bool,
) -> ParseResult:
    title = _clean_title(_first_text(guess.get("title")))
    year = _int(guess.get("year"))
    episodes = _ints(guess.get("episode"))
    guessed_date = guess.get("date")
    air_date = guessed_date if isinstance(guessed_date, date) else None
    absolute_match = None
    if not explicit_in_name and not tags.arabic_episodes and air_date is None:
        absolute_match = _ABSOLUTE.match(stem)
    if absolute_match:
        # guessit splits numbers off titles here ("Mob Psycho 100 - 05", "86 - 01").
        title, title_year = _split_title_year(absolute_match["title"])
        year = title_year or year
        episodes = (int(absolute_match["episode"]),)
    if tags.arabic_episodes:
        episodes = tags.arabic_episodes
    if air_date is not None and not _EPISODE_MARKER.search(stem) and not tags.arabic_episodes:
        # "60.Minutes.2024.03.17": numbers before the date belong to the title.
        episodes = ()
        date_match = _DATE.search(stem)
        prefix = _clean_title(_words(stem[: date_match.start()])) if date_match else None
        if prefix:
            title = prefix

    season = _int(guess.get("season"))
    if hints.season is not None and (season is None or not explicit_in_name):
        season = hints.season
    elif not explicit_in_path:
        season = None  # guessit derived it from a series year or a bare number

    title = _series_title(title, hints)
    year = _series_year(year, title, hints)
    if absolute_match and hints.season and len(episodes) == 1:
        episodes = _see_episode(episodes[0], hints.season)

    absolute = None
    if season is None and episodes and air_date is None:
        absolute, episodes = episodes[0], ()

    episode_title = _clean_title(_first_text(guess.get("episode_title")))
    if episode_title and (
        _is_language_word(episode_title) or normalize(episode_title) == normalize(title or "")
    ):
        episode_title = None
    return ParseResult(
        kind="episode",
        title=title,
        year=year,
        season=season,
        episodes=episodes,
        absolute_episode=absolute,
        air_date=air_date,
        episode_title=episode_title,
    )


def _series_title(title: str | None, hints: _FolderHints) -> str | None:
    if hints.season is not None and hints.show_title:
        return hints.show_title  # inside `Show/Season N/`, the show folder names the series
    if not title or (hints.parent_title and normalize(title) == normalize(hints.parent_title)):
        return hints.parent_title
    return title


def _series_year(year: int | None, title: str | None, hints: _FolderHints) -> int | None:
    if year is not None and title and normalize(title) == str(year):
        year = None  # "1923.S01E01.1923": a number-only title, not a year
    if year is not None and year == hints.season:
        year = None  # `Show/Season 2024/…`: the season folder, not the series year
    return year or hints.show_year


def _see_episode(number: int, season: int) -> tuple[int, ...]:
    """`Show/Season 01/Show - 101.mkv` is the old `SEE` form of S01E01."""
    digits, prefix = str(number), str(season)
    if len(digits) in {3, 4} and digits.startswith(prefix):
        rest = digits[len(prefix) :]
        if len(rest) == 2 and int(rest) > 0:
            return (int(rest),)
    return (number,)


# --- guessit glue ----------------------------------------------------------------------


def _guess(
    text: str, kind: Literal["movie", "episode"] | None, *, episode_prefer_number: bool = False
) -> dict[str, object]:
    options: dict[str, object] = {}
    if kind is not None:
        options["type"] = kind
    if episode_prefer_number:
        options["episode_prefer_number"] = True
    return cast("dict[str, object]", guessit(text, options))


@lru_cache(maxsize=2048)
def _guess_folder(text: str, kind: Literal["movie", "episode"] | None) -> Mapping[str, object]:
    """Folder names repeat for every file inside them; parse each one once."""
    return MappingProxyType(_guess(text, kind))


def _has_episode_or_date(guess: Mapping[str, object]) -> bool:
    return guess.get("episode") is not None or guess.get("date") is not None


def _merge_ids(ids: ProviderIds, guess: Mapping[str, object]) -> ProviderIds:
    """Our own tag parsing wins; guessit fills gaps (it reads a few more spellings)."""
    imdb = _text(guess.get("imdb_id"))
    return ProviderIds(
        tmdb=ids.tmdb if ids.tmdb is not None else _int(guess.get("tmdb_id")),
        imdb=ids.imdb if ids.imdb is not None else (imdb.lower() if imdb else None),
        tvdb=ids.tvdb if ids.tvdb is not None else _int(guess.get("tvdb_id")),
    )


def _merge_languages(found: list[str], guessed: object) -> tuple[str, ...]:
    codes = list(found)
    for value in guessed if isinstance(guessed, list) else [guessed]:
        code = "" if value is None else str(value)
        if code and code != "und":
            codes.append(code)
    return tuple(dict.fromkeys(codes))


def _edition(value: object, stem: str) -> str | None:
    values = value if isinstance(value, list) else [value]
    labels: list[str] = []
    for item in values:
        text = _text(item)
        if text is None:
            continue
        label = _EDITION_LABELS.get(text.casefold(), text)
        if label == "Ultimate Edition" and re.search(r"ultimate[ ._-]*cut", stem, re.IGNORECASE):
            label = "Ultimate Cut"
        labels.append(label.removeprefix("The ").removeprefix("the "))
    return ", ".join(dict.fromkeys(labels)) or None


def _title_part(stem: str, year: int | None) -> str | None:
    """The "Part Two" / "Vol 1" of a sequel title: written before the year."""
    year_at = stem.find(str(year)) if year else -1
    for match in _TITLE_PART.finditer(stem):
        if year_at == -1 or match.start() < year_at:
            return f"{match['word']} {match['number']}"
    return None


def _split_title_year(text: str) -> tuple[str | None, int | None]:
    match = _TITLE_YEAR.match(text)
    if match:
        return _clean_title(match["title"]), int(match["year"])
    return _clean_title(text), None


def _clean_title(value: str | None) -> str | None:
    if not value:
        return None
    title = _AR_TITLE_PREFIX.sub("", value.strip())
    return title or None


def _words(text: str) -> str:
    return re.sub(r"[\s._]+", " ", text).strip(" -")


def _restore_digits(title: str | None, digits: str | None) -> str | None:
    """Arabic titles keep the digit family they were written in ("الفيل الأزرق ٢")."""
    if title is None or digits is None or not _ARABIC_LETTERS.search(title):
        return title
    return title.translate(str.maketrans("0123456789", digits))


def _is_language_word(text: str) -> bool:
    return all(word in _LANGUAGE_WORDS for word in normalize(text).split())


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_text(value: object) -> str | None:
    if isinstance(value, list):
        return _text(value[0]) if value else None
    return _text(value)


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, list) and value and isinstance(value[0], int):
        return int(value[0])
    return None


def _ints(value: object) -> tuple[int, ...]:
    items = value if isinstance(value, list) else [value]
    numbers = {item for item in items if isinstance(item, int) and not isinstance(item, bool)}
    return tuple(sorted(numbers))
