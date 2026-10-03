"""The parser against the ground truth in backend/tests/data/filenames.csv (SPEC §15).

Every row is its own test case. The library kind comes from the row's notes
("(movies library)", "(series library)"), otherwise the library is mixed.
Titles are compared case- and punctuation-insensitively ("Mr. Robot" equals
"Mr Robot"); everything else must match exactly.
"""

import csv
import re
import unicodedata
from pathlib import Path

import pytest

from apps.library.parsing import LibraryKind, ParseResult, parse_path

CSV_PATH = Path(__file__).resolve().parents[3] / "tests" / "data" / "filenames.csv"
FIELDS = (
    "kind",
    "title",
    "year",
    "season",
    "episodes",
    "absolute_episode",
    "air_date",
    "edition",
    "tmdb_id_hint",
    "imdb_id_hint",
    "tvdb_id_hint",
)


def _rows() -> list[dict[str, str]]:
    with CSV_PATH.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _fold_title(text: str) -> str:
    folded = unicodedata.normalize("NFC", text).casefold()
    return " ".join(re.sub(r"[\W_]+", " ", folded).split())


def _library_kind(notes: str) -> LibraryKind:
    if "(movies library)" in notes:
        return "movies"
    if "(series library)" in notes:
        return "series"
    return "mixed"


def _as_row(result: ParseResult) -> dict[str, str]:
    if result.kind == "ignore":
        return dict.fromkeys(FIELDS, "") | {"kind": "ignore"}
    return {
        "kind": result.kind,
        "title": _fold_title(result.title or ""),
        "year": str(result.year) if result.year is not None else "",
        "season": str(result.season) if result.season is not None else "",
        "episodes": ";".join(str(number) for number in result.episodes),
        "absolute_episode": str(result.absolute_episode or ""),
        "air_date": result.air_date.isoformat() if result.air_date else "",
        "edition": (result.edition or "").replace(", ", ";"),
        "tmdb_id_hint": str(result.provider_ids.tmdb or ""),
        "imdb_id_hint": result.provider_ids.imdb or "",
        "tvdb_id_hint": str(result.provider_ids.tvdb or ""),
    }


ROWS = _rows()


def test_the_ground_truth_is_big_enough() -> None:
    assert len(ROWS) >= 300


@pytest.mark.parametrize("row", ROWS, ids=[f"line{n}" for n in range(2, len(ROWS) + 2)])
def test_parse_matches_ground_truth(row: dict[str, str]) -> None:
    result = parse_path(row["path"], library_kind=_library_kind(row["notes"]))
    expected = {field: row[field] for field in FIELDS}
    expected["title"] = _fold_title(expected["title"])
    assert _as_row(result) == expected, row["notes"]
