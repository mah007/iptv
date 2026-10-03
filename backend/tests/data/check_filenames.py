"""Validate filenames.csv, the ground truth for the library parser (SPEC §7.2 and §15).

    python3 backend/tests/data/check_filenames.py [CSV]

Checks the header, the format of every value and that no path appears twice, even on a
case-insensitive file system. It also checks that each expected value can be read from its
path: title words, year, SxxEyy and NxMM markers, episode numbers, air dates, editions and
provider ids. This catches typos in the expectations, but it does not parse the names.
README.md in this folder documents the columns and conventions. Standard library only.
"""

from __future__ import annotations

import csv
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterable
from datetime import date
from pathlib import Path

DEFAULT_CSV = Path(__file__).with_name("filenames.csv")
MIN_ROWS = 320

COLUMNS = (
    "path",
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
    "notes",
)
# What a parser must produce; all of it stays empty for kind=ignore.
EXPECTED = COLUMNS[2:12]
KINDS = frozenset({"movie", "episode", "ignore"})
VIDEO_EXTENSIONS = frozenset({"mkv", "mp4", "m4v", "mov", "avi", "ts", "m2ts", "webm"})  # §7.1
WINDOWS_INVALID = frozenset('<>:"|?*\\')

# Canonical edition labels, each with a word the path must contain. Any other value must
# come verbatim from an explicit Plex tag, {edition-<value>}.
EDITIONS = {
    "Director's Cut": "director",
    "Extended": "extended",
    "Theatrical": "theatrical",
    "Unrated": "unrated",
    "Uncut": "uncut",
    "Remastered": "remaster",
    "IMAX": "imax",
    "Final Cut": "final",
    "Special Edition": "special",
    "Ultimate Edition": "ultimate",
    "Ultimate Cut": "ultimate",
    "Collector's Edition": "collector",
    "Criterion": "criterion",
}

ARABIC_INDIC_DIGITS = str.maketrans({chr(0x0660 + digit): str(digit) for digit in range(10)})
ARABIC_SCRIPT = re.compile(r"[؀-ۿ]")
WORD = re.compile(r"[^\W_]+")
DIGITS = re.compile(r"\d+")
NUMBER = re.compile(r"0|[1-9]\d*")
NUMBER_LIST = re.compile(r"(0|[1-9]\d*)(;(0|[1-9]\d*))*")
IMDB_ID = re.compile(r"tt\d{7,8}")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SXXEYY = re.compile(r"(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,3})", re.IGNORECASE)
NXMM = re.compile(r"(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?![0-9])", re.IGNORECASE)
PROVIDER_TAG = re.compile(r"[\[{](tmdb|imdb|tvdb)(?:id)?[-=]([^\]}]*)[\]}]", re.IGNORECASE)


def fold(text: str) -> str:
    """NFC, case-folded, Arabic-Indic digits as ASCII: the form used for comparisons."""
    return unicodedata.normalize("NFC", text).casefold().translate(ARABIC_INDIC_DIGITS)


def numbers_in(text: str) -> set[int]:
    return {int(run) for run in DIGITS.findall(fold(text))}


def check_path(path: str, notes: str) -> list[str]:
    errors: list[str] = []
    parts = path.split("/")
    if not path or path.startswith(("/", "./")):
        errors.append("path must be relative to the library root")
    if any(part in {"", ".", ".."} or part != part.strip() for part in parts):
        errors.append("path has an empty, dot or padded component")
    if WINDOWS_INVALID & set(path):
        errors.append("path has a character that is invalid on Windows")
    is_nfc = unicodedata.is_normalized("NFC", path)
    if is_nfc == ("NFD" in notes):
        errors.append("only rows whose notes say NFD may (and must) have a non-NFC path")
    return errors


def check_formats(row: dict[str, str]) -> list[str]:
    errors = [
        f"{column} is not NFC (only a path may be decomposed)"
        for column in (*EXPECTED, "notes")
        if not unicodedata.is_normalized("NFC", row[column])
    ]
    year = row["year"]
    if year and not (year.isascii() and year.isdigit() and 1888 <= int(year) <= 2035):
        errors.append(f"year {year!r} is not a plausible 4-digit year")
    for column in ("season", "absolute_episode", "tmdb_id_hint", "tvdb_id_hint"):
        if row[column] and not NUMBER.fullmatch(row[column]):
            errors.append(f"{column} {row[column]!r} is not a plain integer")
    episodes = row["episodes"]
    if episodes:
        numbers = [int(n) for n in episodes.split(";")] if NUMBER_LIST.fullmatch(episodes) else []
        if not numbers or numbers != sorted(set(numbers)):
            errors.append(f"episodes {episodes!r} must be ascending integers joined by ';'")
    if row["imdb_id_hint"] and not IMDB_ID.fullmatch(row["imdb_id_hint"]):
        errors.append(f"imdb_id_hint {row['imdb_id_hint']!r} is not tt + 7 or 8 digits")
    air_date = row["air_date"]
    if air_date:
        try:
            date.fromisoformat(air_date)
            valid = bool(ISO_DATE.fullmatch(air_date))
        except ValueError:
            valid = False
        if not valid:
            errors.append(f"air_date {air_date!r} is not an ISO date (YYYY-MM-DD)")
    return errors


def check_kind(row: dict[str, str]) -> list[str]:
    kind, path = row["kind"], row["path"]
    if kind == "ignore":
        filled = [column for column in EXPECTED if row[column]]
        problems = [f"kind=ignore must leave {', '.join(filled)} empty"] if filled else []
        return problems + ([] if row["notes"] else ["kind=ignore needs a note saying why"])
    errors: list[str] = []
    if path.rpartition(".")[2].casefold() not in VIDEO_EXTENSIONS:
        errors.append(f"a {kind} needs an accepted video extension (SPEC §7.1)")
    if not row["title"] or row["title"] != row["title"].strip():
        errors.append("title is missing or padded")
    episode_fields = [c for c in ("season", "episodes", "absolute_episode", "air_date") if row[c]]
    if kind == "movie" and episode_fields:
        errors.append(f"a movie cannot have {', '.join(episode_fields)}")
    if kind == "episode":
        if row["edition"]:
            errors.append("editions are for movies")
        if row["episodes"] and not row["season"]:
            errors.append("episodes need a season (use absolute_episode without one)")
        if not (
            row["episodes"] or row["absolute_episode"] or row["air_date"] or row["season"] == "0"
        ):
            errors.append("an episode needs episodes, absolute_episode, air_date or season 0")
    return errors


def check_markers(row: dict[str, str]) -> list[str]:
    """Season, episode, absolute number and air date must be readable from the path."""
    path, season, episodes = row["path"], row["season"], row["episodes"]
    filename = path.rpartition("/")[2]
    marker = SXXEYY.search(filename) or NXMM.search(filename)
    errors: list[str] = []
    if marker and season and episodes:
        if (int(marker[1]), int(marker[2])) != (int(season), int(episodes.split(";")[0])):
            errors.append(
                f"marker {marker[0]!r} disagrees with season {season}, episodes {episodes}"
            )
    elif season:
        special = season == "0" and "special" in fold(path)
        if int(season) not in numbers_in(path) and not special:
            errors.append(f"season {season} cannot be read from the path")
    found = numbers_in(path)
    missing = [n for n in episodes.split(";") if n and int(n) not in found]
    if missing:
        errors.append(f"episodes {', '.join(missing)} cannot be read from the path")
    if row["absolute_episode"] and int(row["absolute_episode"]) not in found:
        errors.append(f"absolute_episode {row['absolute_episode']} cannot be read from the path")
    if row["air_date"]:
        y, m, d = row["air_date"].split("-")
        forms = (f"{y}.{m}.{d}", f"{y}-{m}-{d}", f"{y}{m}{d}", f"{d}.{m}.{y}")
        if not any(form in path for form in forms):
            errors.append(f"air_date {row['air_date']} cannot be read from the path")
    return errors


def provider_ids(path: str) -> tuple[dict[str, str], list[str]]:
    seen: dict[str, set[str]] = {}
    for provider, value in PROVIDER_TAG.findall(path):
        if value:
            seen.setdefault(f"{provider.lower()}_id_hint", set()).add(value)
    conflicts = [f"{column} has conflicting tags" for column, ids in seen.items() if len(ids) > 1]
    return {column: min(ids) for column, ids in seen.items()}, conflicts


def check_readable(row: dict[str, str]) -> list[str]:
    """Title words, year, edition and provider ids must come from the path."""
    path = row["path"]
    folded = fold(path)
    errors: list[str] = []
    missing = set(WORD.findall(fold(row["title"]))) - set(WORD.findall(folded))
    if missing:
        errors.append(f"title words {sorted(missing)} are not in the path")
    if row["year"] and row["year"] not in folded:
        errors.append(f"year {row['year']} is not in the path")
    labels = [label for label in row["edition"].split(";") if label]
    if len(labels) != len(set(labels)):
        errors.append("edition lists a label twice")
    for label in labels:
        word = EDITIONS.get(label)
        if word is None and f"{{edition-{label}}}" not in unicodedata.normalize("NFC", path):
            errors.append(
                f"edition {label!r} is neither canonical nor an explicit {{edition-}} tag"
            )
        elif word is not None and word not in folded:
            errors.append(f"edition {label!r} cannot be read from the path")
    ids, conflicts = provider_ids(path)
    errors.extend(conflicts)
    for column in ("tmdb_id_hint", "imdb_id_hint", "tvdb_id_hint"):
        if row[column] != ids.get(column, ""):
            errors.append(f"{column} {row[column]!r} but the path says {ids.get(column, '')!r}")
    return errors


def check_row(row: dict[str, str]) -> list[str]:
    if row["kind"] not in KINDS:
        return [f"kind {row['kind']!r} is not one of {sorted(KINDS)}"]
    errors = check_path(row["path"], row["notes"]) + check_formats(row) + check_kind(row)
    if row["kind"] != "ignore":
        errors += check_markers(row) + check_readable(row)
    return errors


def load(csv_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with csv_path.open(encoding="utf-8", newline="") as handle:
        records = list(csv.reader(handle))
    if not records or tuple(records[0]) != COLUMNS:
        return [], [f"header must be: {','.join(COLUMNS)}"]
    rows: list[dict[str, str]] = []
    errors: list[str] = []
    for line, record in enumerate(records[1:], start=2):
        if len(record) != len(COLUMNS):
            errors.append(f"line {line}: {len(record)} fields, expected {len(COLUMNS)}")
            continue
        rows.append(dict(zip(COLUMNS, record, strict=True)))
    return rows, errors


def summary(rows: Iterable[dict[str, str]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts[f"kind={row['kind']}"] += 1
        flags = {
            "Arabic script": bool(ARABIC_SCRIPT.search(row["path"])),
            "provider id": any(row[c] for c in ("tmdb_id_hint", "imdb_id_hint", "tvdb_id_hint")),
            "edition": bool(row["edition"]),
            "multi-episode": ";" in row["episodes"],
            "absolute number": bool(row["absolute_episode"]),
            "dated episode": bool(row["air_date"]),
            "special (season 0)": row["season"] == "0",
            "year from the folder only": bool(row["year"])
            and row["year"] not in row["path"].rpartition("/")[2],
            "sample_media.sh": row["notes"].startswith("sample_media.sh"),
        }
        counts.update(name for name, present in flags.items() if present)
    return counts


def main(argv: list[str]) -> int:
    csv_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_CSV
    rows, errors = load(csv_path)
    keys = Counter(unicodedata.normalize("NFC", row["path"]).casefold() for row in rows)
    errors += [f"duplicate path (NFC, case-insensitive): {key}" for key, n in keys.items() if n > 1]
    for line, row in enumerate(rows, start=2):
        errors += [f"line {line} ({row['path']}): {error}" for error in check_row(row)]
    if len(rows) < MIN_ROWS:
        errors.append(f"{len(rows)} rows; at least {MIN_ROWS} are required")
    out = sys.stdout
    out.write(f"{csv_path}: {len(rows)} rows\n")
    for name, count in sorted(summary(rows).items()):
        out.write(f"  {name:<28} {count:>4}\n")
    if errors:
        sys.stderr.write("".join(f"error: {error}\n" for error in errors))
        sys.stderr.write(f"{len(errors)} error(s)\n")
        return 1
    out.write("OK\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
