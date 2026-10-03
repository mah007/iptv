#!/usr/bin/env python3
"""Xtream contract suite for Smart IPTV (SPEC §7.5).

Checks what IPTV apps receive from the Xtream host against the JSON Schemas in
compat/schemas, the golden fixtures in compat/fixtures and the invalid cases in
compat/fixtures/invalid; with --live, also against a running server:

    uvx --with 'jsonschema==4.*' python compat/validate.py
    XC_USER=... XC_PASS=... uvx --with 'jsonschema==4.*' python compat/validate.py \\
        --live http://tv.localhost:8080

Every problem names its place (a JSON path, an M3U line or an XMLTV element), what
was found there and the rule it breaks. Credentials never appear in the output.
Exit status: 0 when every check passed, 1 when one failed, 2 when the suite could
not run. load_suite(), check_m3u() and check_xmltv() can also be imported by tests
(compat/README.md).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import functools
import gzip
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, TextIO
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlencode, urlsplit

ROOT = Path(__file__).resolve().parent
SCHEMA_DIR = ROOT / "schemas"
FIXTURE_DIR = ROOT / "fixtures"
INVALID_DIR = FIXTURE_DIR / "invalid"
SCHEMA_BASE = "https://smart-iptv.invalid/compat/schemas/"
DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
USER_AGENT = "smart-iptv-compat/1"

#: The JSON contracts, in report order. Each has compat/schemas/<name>.schema.json
#: and the golden compat/fixtures/<name>.json.
ACTIONS = (
    "login",
    "auth_failure",
    "get_vod_categories",
    "get_series_categories",
    "get_live_categories",
    "get_vod_streams",
    "get_vod_info",
    "get_series",
    "get_series_info",
    "get_live_streams",
    "get_short_epg",
    "get_simple_data_table",
)
#: Golden playlists and the live-channel extension each was requested with (output=).
M3U_FIXTURES = {"m3u_plus.m3u": "ts"}
#: get.php output= values and the extension of live URLs in each (compat/m3u.md):
#: movies and episodes are always .mp4, and live is never MP4, so mp4 keeps .ts.
PLAYLIST_OUTPUTS = {"ts": "ts", "m3u8": "m3u8", "mp4": "ts"}
XMLTV_FIXTURES = ("xmltv.xml",)
#: Values under these keys are credentials: never printed.
SENSITIVE_KEYS = frozenset({"password", "username"})

DATE_KEYS = frozenset({"release_date", "releasedate", "releaseDate", "air_date"})
DATETIME_KEYS = frozenset({"time_now", "start", "end"})
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
IMAGE_URL = re.compile(r"https?://[^\s\"<>]+\.(jpe?g|png|webp)(\?[^\s\"<>]*)?")


class SuiteError(Exception):
    """The suite itself cannot run (missing dependency, broken schema or case file)."""


class LiveError(Exception):
    """A request to the live server failed before it produced a response."""


@dataclass(frozen=True)
class Problem:
    """One contract violation: where it is, which rule it breaks, and what was found."""

    where: str
    kind: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message} [{self.kind}]"


# --------------------------------------------------------------------------- output


_PATH_CREDENTIALS = re.compile(r"/(movie|series|live|timeshift)/[^/\s]+/[^/\s]+/")
_QUERY_CREDENTIALS = re.compile(r"(?i)\b(username|password)=[^&\s\"'<>]*")


class Redactor:
    """Strips credentials from all output: known secrets, Xtream play paths, query values."""

    def __init__(self) -> None:
        self._secrets: list[str] = []

    def add(self, secret: str) -> None:
        # Very short values would garble the output; the patterns below still cover them.
        if len(secret) >= 3:
            for form in (secret, quote(secret, safe=""), quote_plus(secret)):
                if form not in self._secrets:
                    self._secrets.append(form)
            self._secrets.sort(key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        text = _PATH_CREDENTIALS.sub(lambda m: f"/{m.group(1)}/***/***/", text)
        return _QUERY_CREDENTIALS.sub(lambda m: f"{m.group(1)}=***", text)


class Reporter:
    """Prints PASS/FAIL/SKIP lines (always redacted) and counts them."""

    def __init__(self, redact: Redactor, *, verbose: bool, stream: TextIO | None = None) -> None:
        self.redact = redact
        self.verbose = verbose
        self.stream = stream or sys.stdout
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def write(self, text: str = "") -> None:
        self.stream.write(self.redact(text) + "\n")
        self.stream.flush()

    def section(self, title: str) -> None:
        self.write()
        self.write(title)

    def ok(self, label: str, detail: str = "") -> None:
        self.passed += 1
        self.write(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))

    def fail(self, label: str, problems: Sequence[Problem]) -> None:
        self.failed += 1
        self.write(f"  FAIL  {label}")
        for problem in problems:
            self.write(f"        {problem}")

    def skip(self, label: str, reason: str) -> None:
        self.skipped += 1
        self.write(f"  SKIP  {label}  ({reason})")

    def result(self, label: str, problems: Sequence[Problem], detail: str = "") -> bool:
        if problems:
            self.fail(label, problems)
            return False
        self.ok(label, detail)
        return True


def describe(value: Any, *, sensitive: bool = False) -> str:
    """A short, typed description of a JSON value for problem messages."""
    if isinstance(value, str):
        shown = value if len(value) <= 60 else value[:57] + "..."
        hidden = "a string (value hidden)"
        return hidden if sensitive else f"string {json.dumps(shown, ensure_ascii=False)}"
    if isinstance(value, list | dict):
        kind, noun = ("array of", "item") if isinstance(value, list) else ("object with", "key")
        return f"{kind} {len(value)} {noun}{'' if len(value) == 1 else 's'}"
    if value is None or isinstance(value, bool):
        return "null" if value is None else f"boolean {json.dumps(value)}"
    return f"{'integer' if isinstance(value, int) else 'number'} {value!r}"


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def json_path(parts: Iterable[str | int]) -> str:
    """$-rooted path: $.user_info.exp_date, $[0].stream_id, $.episodes["1"][0].id."""
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        elif _IDENTIFIER.fullmatch(part):
            path += f".{part}"
        else:
            path += f"[{json.dumps(part, ensure_ascii=False)}]"
    return path


# --------------------------------------------------------------------------- strict JSON


class _JsonValueError(ValueError):
    pass


def _reject_constant(name: str) -> NoReturn:
    raise _JsonValueError(f"{name} is not valid JSON")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _JsonValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def parse_json(data: bytes) -> tuple[Any, list[Problem]]:
    """Parse strictly: UTF-8 without BOM, no NaN/Infinity, no duplicate keys."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, [Problem("$", "json", f"not UTF-8 (invalid byte at offset {exc.start})")]
    if text.startswith("\ufeff"):
        return None, [Problem("$", "json", "starts with a byte order mark")]
    try:
        value = json.loads(
            text, parse_constant=_reject_constant, object_pairs_hook=_reject_duplicate_keys
        )
    except json.JSONDecodeError as exc:
        return None, [
            Problem(
                "$", "json", f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
            )
        ]
    except _JsonValueError as exc:
        return None, [Problem("$", "json", str(exc))]
    return value, []


# --------------------------------------------------------------------------- schemas


@functools.lru_cache(maxsize=1024)
def ecma_regex(pattern: str) -> re.Pattern[str]:
    """Compile a schema pattern so a final `$` matches only at the very end, as in ECMA-262.

    Python's `$` also matches before a trailing newline, so "1822521600\\n" would pass
    ^[0-9]+$; JSON Schema patterns follow ECMA-262, where it does not.
    """
    if pattern.endswith("$"):
        body = pattern[:-1]
        if (len(body) - len(body.rstrip("\\"))) % 2 == 0:
            pattern = body + r"\Z"
    return re.compile(pattern)


def _import_jsonschema() -> Any:
    try:
        import jsonschema  # noqa: PLC0415 - optional dependency, reported clearly below
        import referencing  # noqa: F401, PLC0415
    except ImportError as exc:
        raise SuiteError(
            "jsonschema 4.18+ is required. Run: uvx --with 'jsonschema==4.*' python "
            "compat/validate.py"
        ) from exc
    from importlib.metadata import version  # noqa: PLC0415

    major, minor = (int(part) for part in version("jsonschema").split(".")[:2])
    if (major, minor) < (4, 18):
        raise SuiteError(f"jsonschema {major}.{minor} is too old: 4.18+ is required")
    return jsonschema


def _strict_validator_class() -> Any:
    from jsonschema import Draft202012Validator, validators  # noqa: PLC0415
    from jsonschema.exceptions import ValidationError  # noqa: PLC0415

    def is_integer(_checker: Any, instance: Any) -> bool:
        # 1.0 is a number, not an integer: PHP panels and orjson write integers without
        # a fraction, and strict client parsers reject 1.0 where they expect an int.
        return isinstance(instance, int) and not isinstance(instance, bool)

    def pattern(validator: Any, value: str, instance: Any, _schema: Any) -> Iterator[Any]:
        if validator.is_type(instance, "string") and not ecma_regex(value).search(instance):
            yield ValidationError(f"{instance!r} does not match {value!r}")

    extend: Any = validators.extend  # untyped in the jsonschema stubs
    return extend(
        Draft202012Validator,
        validators={"pattern": pattern},
        type_checker=Draft202012Validator.TYPE_CHECKER.redefine("integer", is_integer),
    )


_SINGLE_SUBSCHEMA = ("items", "additionalProperties", "propertyNames", "not", "contains")
_MAPPED_SUBSCHEMAS = ("properties", "$defs", "patternProperties")
_LISTED_SUBSCHEMAS = ("allOf", "anyOf", "oneOf", "prefixItems")


def _walk_schema(node: Any, pointer: str, role: str) -> Iterator[tuple[str, str, dict[str, Any]]]:
    if not isinstance(node, dict):
        return
    yield pointer, role, node
    for keyword in _SINGLE_SUBSCHEMA:
        if keyword in node:
            yield from _walk_schema(node[keyword], f"{pointer}/{keyword}", keyword)
    for keyword in _MAPPED_SUBSCHEMAS:
        for key, child in (node.get(keyword) or {}).items():
            child_role = "property" if keyword == "properties" else keyword
            escaped = key.replace("~", "~0").replace("/", "~1")
            yield from _walk_schema(child, f"{pointer}/{keyword}/{escaped}", child_role)
    for keyword in _LISTED_SUBSCHEMAS:
        for index, child in enumerate(node.get(keyword) or []):
            yield from _walk_schema(child, f"{pointer}/{keyword}/{index}", keyword)


def lint_schema(file_name: str, schema: Any) -> list[Problem]:
    """House rules that keep every schema deliberate (README: "Schema rules")."""
    where = f"schemas/{file_name}"
    if not isinstance(schema, dict):
        return [Problem(where, "schema-lint", "a schema file must hold a JSON object")]
    problems = []
    if schema.get("$schema") != DRAFT_2020_12:
        problems.append(Problem(where, "schema-lint", f"$schema must be {DRAFT_2020_12}"))
    if schema.get("$id") != SCHEMA_BASE + file_name:
        problems.append(Problem(where, "schema-lint", f"$id must be {SCHEMA_BASE + file_name}"))
    for pointer, role, node in _walk_schema(schema, "", "root"):
        at = f"{where}#{pointer or '/'}"
        problems += [Problem(at, "schema-lint", message) for message in _lint_node(role, node)]
    return problems


def _lint_node(role: str, node: dict[str, Any]) -> Iterator[str]:
    types = node.get("type")
    type_list = types if isinstance(types, list) else [types]
    if "null" in type_list:
        yield 'null is never allowed: use "" or []'
    if role == "property" and not {"type", "$ref", "const", "enum"} & node.keys():
        yield "every field needs a type, $ref, const or enum"
    if "object" in type_list:
        if "additionalProperties" not in node:
            yield "objects must set additionalProperties deliberately"
        properties = node.get("properties", {})
        if properties and sorted(node.get("required", [])) != sorted(properties):
            yield "required must list exactly the properties: PHP panels send every field"
    if "array" in type_list and "items" not in node:
        yield "arrays must define items"
    pattern = node.get("pattern")
    if isinstance(pattern, str) and not (pattern.startswith("^") and pattern.endswith("$")):
        yield "patterns must be anchored with ^ and $"


def _ref_values(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _ref_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from _ref_values(item)


# --------------------------------------------------------------------------- invariants


Invariant = Callable[[Any], Iterator[Problem]]


def _utc_text(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime(DATETIME_FORMAT)


def _real_dates(node: Any, path: tuple[str | int, ...] = ()) -> Iterator[Problem]:
    """Patterns accept 2026-02-30; this check needs real calendar dates."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and value and key in DATE_KEYS | DATETIME_KEYS:
                fmt = "%Y-%m-%d" if key in DATE_KEYS else DATETIME_FORMAT
                try:
                    datetime.strptime(value, fmt).replace(tzinfo=UTC)
                except ValueError:
                    yield Problem(
                        json_path((*path, key)),
                        "invariant:real-date",
                        f"found {describe(value)}, which is not a real date",
                    )
            else:
                yield from _real_dates(value, (*path, key))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _real_dates(item, (*path, index))


def _numbering(items: list[Any]) -> Iterator[Problem]:
    for index, item in enumerate(items):
        if item["num"] != index + 1:
            yield Problem(
                f"$[{index}].num",
                "invariant:numbering",
                f"found {item['num']}, expected {index + 1}: num counts this "
                "response's items from 1",
            )


def _unique(items: list[Any], key: str, base: str = "$") -> Iterator[Problem]:
    first: dict[Any, int] = {}
    for index, item in enumerate(items):
        value = item[key]
        if value in first:
            yield Problem(
                f"{base}[{index}].{key}",
                "invariant:unique-ids",
                f"{describe(value)} repeats {base}[{first[value]}].{key}",
            )
        else:
            first[value] = index


def _category_ids(item: Any, where: str) -> Iterator[Problem]:
    ids = item["category_ids"]
    if not ids or ids[0] != int(item["category_id"]):
        yield Problem(
            f"{where}.category_ids",
            "invariant:category-ids",
            f"found {ids}, but category_ids must start with category_id {item['category_id']!r}",
        )


def _rating_scale(item: Any, where: str) -> Iterator[Problem]:
    expected = float(item["rating"]) / 2 if item["rating"] else 0.0
    if abs(item["rating_5based"] - expected) > 0.051:  # rating / 2, rounded to one decimal
        yield Problem(
            f"{where}.rating_5based",
            "invariant:rating-scale",
            f"found {describe(item['rating_5based'])}, expected {expected:.2f} "
            f"(rating {item['rating']!r} / 2, one decimal)",
        )


def _login(payload: Any) -> Iterator[Problem]:
    server = payload["server_info"]
    parsed = datetime.strptime(server["time_now"], DATETIME_FORMAT).replace(tzinfo=UTC)
    if abs(parsed.timestamp() - server["timestamp_now"]) > 1:
        yield Problem(
            "$.server_info.time_now",
            "invariant:clock",
            f"found {describe(server['time_now'])}, but timestamp_now is "
            f"{_utc_text(server['timestamp_now'])} UTC",
        )


def _categories(payload: Any) -> Iterator[Problem]:
    yield from _unique(payload, "category_id")


def _stream_list(payload: Any) -> Iterator[Problem]:
    yield from _numbering(payload)
    yield from _unique(payload, "stream_id")
    for index, item in enumerate(payload):
        yield from _category_ids(item, f"$[{index}]")
        if "rating" in item:
            yield from _rating_scale(item, f"$[{index}]")
        if item.get("stream_type") == "live":
            if item["tv_archive"] == 0 and item["tv_archive_duration"] != 0:
                yield Problem(
                    f"$[{index}].tv_archive_duration",
                    "invariant:archive",
                    "must be 0 when tv_archive is 0",
                )
            if item["tv_archive"] == 1 and item["tv_archive_duration"] < 1:
                yield Problem(
                    f"$[{index}].tv_archive_duration",
                    "invariant:archive",
                    "must be at least 1 (day) when tv_archive is 1",
                )


def _series_list(payload: Any) -> Iterator[Problem]:
    yield from _numbering(payload)
    yield from _unique(payload, "series_id")
    for index, item in enumerate(payload):
        yield from _category_ids(item, f"$[{index}]")
        yield from _rating_scale(item, f"$[{index}]")


def _vod_info(payload: Any) -> Iterator[Problem]:
    for key, value in payload["info"].items():
        if payload[key] != value:
            yield Problem(
                f"$.{key}",
                "invariant:root-copy",
                f"found {describe(payload[key])}, but $.info.{key} is "
                f"{describe(value)}: get_vod_info repeats every info field at the root",
            )
    yield from _rating_scale(payload["info"], "$.info")


def _series_info(payload: Any) -> Iterator[Problem]:
    episodes, seasons = payload["episodes"], payload["seasons"]
    keys = [int(key) for key in episodes]
    if keys != sorted(keys):
        yield Problem(
            "$.episodes",
            "invariant:season-order",
            f'keys {list(episodes)} must be in numeric order ("2" before "10")',
        )
    numbers = [season["season_number"] for season in seasons]
    if numbers != sorted(set(numbers)) or set(numbers) != set(keys):
        yield Problem(
            "$.seasons",
            "invariant:seasons",
            f"season_number values {numbers} must be the episodes keys "
            f"{sorted(keys)}, once each and in order",
        )
    for index, season in enumerate(seasons):
        listed = episodes.get(str(season["season_number"]))
        if listed is not None and season["episode_count"] != len(listed):
            yield Problem(
                f"$.seasons[{index}].episode_count",
                "invariant:seasons",
                f"found {season['episode_count']}, but season "
                f"{season['season_number']} lists {len(listed)} episodes",
            )
    first_seen: dict[str, str] = {}
    for key, items in episodes.items():
        episode_numbers = [episode["episode_num"] for episode in items]
        if episode_numbers != sorted(set(episode_numbers)):
            yield Problem(
                json_path(("episodes", key)),
                "invariant:episode-order",
                f"episode_num values {episode_numbers} must be unique and ascending",
            )
        for index, episode in enumerate(items):
            where = json_path(("episodes", key, index))
            if episode["season"] != int(key):
                yield Problem(
                    f"{where}.season",
                    "invariant:episode-season",
                    f"found {episode['season']}, but the episode is listed under "
                    f"season key {key!r}",
                )
            if episode["id"] in first_seen:
                yield Problem(
                    f"{where}.id",
                    "invariant:unique-ids",
                    f"episode id {episode['id']!r} repeats {first_seen[episode['id']]}",
                )
            first_seen.setdefault(episode["id"], f"{where}.id")
    yield from _category_ids(payload["info"], "$.info")
    yield from _rating_scale(payload["info"], "$.info")


def _epg(payload: Any) -> Iterator[Problem]:
    listings = payload["epg_listings"]
    previous_start = None
    for index, item in enumerate(listings):
        where = f"$.epg_listings[{index}]"
        start, stop = int(item["start_timestamp"]), int(item["stop_timestamp"])
        if stop <= start:
            yield Problem(
                f"{where}.stop_timestamp",
                "invariant:epg-times",
                "must be later than start_timestamp",
            )
        for key, timestamp in (("start", start), ("end", stop)):
            if item[key] != _utc_text(timestamp):
                yield Problem(
                    f"{where}.{key}",
                    "invariant:epg-times",
                    f"found {describe(item[key])}, expected "
                    f"{_utc_text(timestamp)!r} (the timestamp in UTC)",
                )
        if previous_start is not None and start < previous_start:
            yield Problem(
                f"{where}.start_timestamp",
                "invariant:epg-order",
                "listings must be ordered by start time",
            )
        previous_start = start
        for key in ("title", "description"):
            try:
                base64.b64decode(item[key], validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError):
                yield Problem(
                    f"{where}.{key}", "invariant:base64-utf8", "does not decode to UTF-8 text"
                )
        if item["channel_id"] != listings[0]["channel_id"]:
            yield Problem(
                f"{where}.channel_id",
                "invariant:epg-channel",
                "every listing must belong to the requested channel",
            )
    playing = [index for index, item in enumerate(listings) if item["now_playing"] == 1]
    if len(playing) > 1:
        yield Problem(
            "$.epg_listings",
            "invariant:now-playing",
            f"listings {playing} all have now_playing 1; one programme plays at a time",
        )
    if playing:
        # Catch-up replays a finished recording: the programme on air now and every
        # later one cannot have it yet.
        on_air = int(listings[playing[0]]["start_timestamp"])
        for index, item in enumerate(listings):
            if item["has_archive"] == 1 and int(item["start_timestamp"]) >= on_air:
                yield Problem(
                    f"$.epg_listings[{index}].has_archive",
                    "invariant:archive",
                    "found 1 on a programme that has not ended; only finished "
                    "programmes inside the catch-up window have an archive",
                )


def _short_epg(payload: Any) -> Iterator[Problem]:
    listings = payload["epg_listings"]
    playing = [index for index, item in enumerate(listings) if item["now_playing"] == 1]
    if playing and playing[0] != 0:
        yield Problem(
            f"$.epg_listings[{playing[0]}].now_playing",
            "invariant:short-epg",
            "get_short_epg starts at the programme on air: it must be the first listing "
            "(apps show the first two as Now and Next)",
        )


INVARIANTS: dict[str, tuple[Invariant, ...]] = {
    "login": (_login,),
    "get_vod_categories": (_categories,),
    "get_series_categories": (_categories,),
    "get_live_categories": (_categories,),
    "get_vod_streams": (_stream_list,),
    "get_vod_info": (_vod_info,),
    "get_series": (_series_list,),
    "get_series_info": (_series_info,),
    "get_live_streams": (_stream_list,),
    "get_short_epg": (_epg, _short_epg),
    "get_simple_data_table": (_epg,),
}


# --------------------------------------------------------------------------- suite


class SchemaSuite:
    """The schemas, a strict 2020-12 validator per action, and the semantic invariants."""

    def __init__(self, schema_dir: Path = SCHEMA_DIR) -> None:
        _import_jsonschema()
        from jsonschema.exceptions import SchemaError  # noqa: PLC0415
        from referencing import Registry, Resource  # noqa: PLC0415
        from referencing.exceptions import Unresolvable  # noqa: PLC0415
        from referencing.jsonschema import DRAFT202012  # noqa: PLC0415

        problems: list[Problem] = []
        documents: dict[str, dict[str, Any]] = {}
        for path in sorted(schema_dir.glob("*.schema.json")):
            document, parse_problems = parse_json(path.read_bytes())
            problems += [Problem(f"schemas/{path.name}", p.kind, p.message) for p in parse_problems]
            if document is not None:
                problems += lint_schema(path.name, document)
                documents[path.name.removesuffix(".schema.json")] = document
        problems += [
            Problem(f"schemas/{action}.schema.json", "schema-lint", "missing")
            for action in ACTIONS
            if action not in documents
        ]
        if problems:
            raise SuiteError("\n".join(str(problem) for problem in problems))

        registry: Any = Registry().with_resources(
            (doc["$id"], Resource.from_contents(doc, default_specification=DRAFT202012))
            for doc in documents.values()
        )
        validator_class = _strict_validator_class()
        for name, document in documents.items():
            try:
                validator_class.check_schema(document)
            except SchemaError as exc:
                problems.append(Problem(f"schemas/{name}.schema.json", "schema", exc.message))
            resolver = registry.resolver(base_uri=document["$id"])
            for ref in _ref_values(document):
                try:
                    resolver.lookup(ref)
                except Unresolvable:
                    problems.append(
                        Problem(
                            f"schemas/{name}.schema.json", "schema", f"$ref {ref} does not resolve"
                        )
                    )
        if problems:
            raise SuiteError("\n".join(str(problem) for problem in problems))
        self.titles = {name: str(documents[name].get("title", name)) for name in ACTIONS}
        self._validators = {
            name: validator_class(documents[name], registry=registry) for name in ACTIONS
        }

    def check(self, action: str, payload: Any) -> list[Problem]:
        """Schema problems; when there are none, the semantic invariants of the action."""
        problems: dict[tuple[str, str], Problem] = {}
        for error in self._validators[action].iter_errors(payload):
            for problem in _problems_from_error(error):
                problems.setdefault((problem.where, problem.kind), problem)
        if problems:
            return list(problems.values())
        found = list(_real_dates(payload))
        for invariant in INVARIANTS.get(action, ()):
            found += invariant(payload)
        return found


def load_suite(schema_dir: Path = SCHEMA_DIR) -> SchemaSuite:
    """Load and lint the schemas; raises SuiteError when they are broken."""
    return SchemaSuite(schema_dir)


def _problems_from_error(error: Any) -> Iterator[Problem]:
    path = list(error.absolute_path)
    keyword, value, instance = error.validator, error.validator_value, error.instance
    if "propertyNames" in error.absolute_schema_path:
        yield Problem(
            json_path(path), "propertyNames", f"key {describe(instance)} does not match {value}"
        )
        return
    if keyword == "required":
        for key in value:
            if isinstance(instance, dict) and key not in instance:
                yield Problem(json_path([*path, key]), "required", "missing; the field is required")
        return
    if keyword == "additionalProperties" and isinstance(instance, dict):
        allowed = error.schema.get("properties", {})
        patterns = error.schema.get("patternProperties", {})
        for key, item in instance.items():
            if key not in allowed and not any(ecma_regex(p).search(key) for p in patterns):
                yield Problem(
                    json_path([*path, key]),
                    "additionalProperties",
                    f"unexpected field ({describe(item, sensitive=key in SENSITIVE_KEYS)});"
                    " only the fields in the schema are allowed",
                )
        return
    sensitive = bool(path) and path[-1] in SENSITIVE_KEYS
    yield Problem(json_path(path), keyword, _value_message(error, sensitive=sensitive))


def _value_message(error: Any, *, sensitive: bool) -> str:
    keyword, value = error.validator, error.validator_value
    found = describe(error.instance, sensitive=sensitive)
    if keyword == "type":
        return f"found {found}, expected {value if isinstance(value, str) else ' or '.join(value)}"
    if keyword == "const":
        return f"found {found}, expected {json.dumps(value, ensure_ascii=False)}"
    if keyword == "enum":
        options = ", ".join(json.dumps(item, ensure_ascii=False) for item in value)
        return f"found {found}, expected one of {options}"
    if keyword == "pattern":
        return f"found {found}, which does not match {value}"
    # Other keywords' messages quote the value, which must stay hidden for credentials.
    if sensitive:
        return f"found {found}, which fails {keyword} {json.dumps(value)}"
    return f"found {found}: {error.message}"


# --------------------------------------------------------------------------- catalog


@dataclass
class Catalog:
    """Responses for one account, for checks that span actions."""

    payloads: dict[str, Any] = field(default_factory=dict)
    series_infos: list[Any] = field(default_factory=list)


_CATEGORY_SOURCES = (
    ("get_vod_streams", "get_vod_categories"),
    ("get_series", "get_series_categories"),
    ("get_live_streams", "get_live_categories"),
)


def check_catalog(catalog: Catalog) -> list[Problem]:
    """Every listed category is visible, and stream ids never collide across kinds."""
    problems = []
    for list_action, category_action in _CATEGORY_SOURCES:
        items, categories = catalog.payloads.get(list_action), catalog.payloads.get(category_action)
        if items is None or categories is None:
            continue
        visible = {int(category["category_id"]) for category in categories}
        for index, item in enumerate(items):
            hidden = [cid for cid in item["category_ids"] if cid not in visible]
            if hidden:
                problems.append(
                    Problem(
                        f"{list_action} $[{index}].category_ids",
                        "invariant:category-visible",
                        f"categories {hidden} are not in {category_action}, so "
                        "apps cannot show the item under them",
                    )
                )
    vod_info, vod_categories = (
        catalog.payloads.get("get_vod_info"),
        catalog.payloads.get("get_vod_categories"),
    )
    if vod_info is not None and vod_categories is not None:
        visible_ids = {category["category_id"] for category in vod_categories}
        if vod_info["movie_data"]["category_id"] not in visible_ids:
            problems.append(
                Problem(
                    "get_vod_info $.movie_data.category_id",
                    "invariant:category-visible",
                    "is not in get_vod_categories",
                )
            )

    # Apps key watch history and favourites by stream id alone, so a movie, an episode and
    # a live channel must never share one. Repeats within one list are _unique's business.
    owners: dict[int, tuple[str, str]] = {}

    def claim(xc_id: int, kind: str, where: str) -> None:
        owner = owners.setdefault(xc_id, (kind, where))
        if owner[0] != kind:
            problems.append(
                Problem(
                    where,
                    "invariant:id-namespace",
                    f"stream id {xc_id} is also used by {owner[1]}: movies, "
                    "episodes and live channels share one id sequence",
                )
            )

    for kind, action in (("movie", "get_vod_streams"), ("live", "get_live_streams")):
        for index, item in enumerate(catalog.payloads.get(action) or []):
            claim(item["stream_id"], kind, f"{action} $[{index}].stream_id")
    for info in catalog.series_infos:
        for key, items in info["episodes"].items():
            for index, episode in enumerate(items):
                where = f"get_series_info {json_path(('episodes', key, index))}.id"
                claim(int(episode["id"]), "episode", where)
    return problems


# --------------------------------------------------------------------------- M3U


_M3U_HEADER = re.compile(r'#EXTM3U((?: [a-z][a-z0-9-]*="[^"\r\n]*")*)')
_M3U_EXTINF = re.compile(r'#EXTINF:(-?[0-9]+)((?: [a-z][a-z0-9-]*="[^"\r\n]*")*),(.*)')
_M3U_ATTRIBUTE = re.compile(r' ([a-z][a-z0-9-]*)="([^"\r\n]*)"')
_M3U_ATTRIBUTES = ("tvg-id", "tvg-name", "tvg-logo", "group-title")
_PLAY_PATH = re.compile(r"/(live|movie|series)/([^/]+)/([^/]+)/([1-9][0-9]{0,15})\.([a-z0-9]+)")
_EPISODE_NAME = re.compile(r".*\S S[0-9]{2,}E[0-9]{2,}")


@dataclass(frozen=True)
class PlaylistEntry:
    line: int
    kind: str  # live | movie | series
    xc_id: int
    name: str
    attributes: dict[str, str]
    url: str


@dataclass
class Playlist:
    epg_url: str = ""
    entries: list[PlaylistEntry] = field(default_factory=list)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _attributes(text: str, where: str, problems: list[Problem]) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for match in _M3U_ATTRIBUTE.finditer(text):
        if match.group(1) in attributes:
            problems.append(Problem(where, "m3u:attributes", f"{match.group(1)} appears twice"))
        attributes[match.group(1)] = match.group(2)
    return attributes


@dataclass(frozen=True)
class _Extinf:
    line: int
    duration: str
    attributes: dict[str, str]
    name: str


@dataclass(frozen=True)
class _PlaylistContext:
    live_ext: str
    origin: str | None
    credentials: tuple[str, str] | None


def check_m3u(
    data: bytes,
    *,
    live_ext: str,
    origin: str | None = None,
    credentials: tuple[str, str] | None = None,
) -> tuple[Playlist, list[Problem]]:
    """Check a get.php?type=m3u_plus playlist (compat/m3u.md).

    live_ext is the output= the playlist was requested with (ts or m3u8). origin is the
    scheme://host[:port] that server_info describes; credentials the pair used to fetch it.
    Without them, url-tvg sets the origin and credentials every URL must share.
    """
    playlist, problems = Playlist(), list[Problem]()
    lines = _m3u_lines(data, problems)
    if lines is None:
        return playlist, problems
    if not lines or not lines[0].startswith("#EXTM3U"):
        problems.append(Problem("line 1", "m3u:header", "must start with #EXTM3U"))
        return playlist, problems
    playlist.epg_url = _m3u_header(lines[0], problems)
    guide_credentials = _guide_credentials(playlist.epg_url)
    if origin and playlist.epg_url and _origin(playlist.epg_url) != origin:
        problems.append(Problem("line 1", "m3u:origin", f"url-tvg must be on {origin}"))
    if credentials and guide_credentials and guide_credentials != credentials:
        problems.append(
            Problem(
                "line 1",
                "m3u:credentials",
                "url-tvg must carry the credentials the playlist was fetched with",
            )
        )
    context = _PlaylistContext(
        live_ext=live_ext,
        origin=origin or (_origin(playlist.epg_url) if playlist.epg_url else None),
        credentials=credentials or guide_credentials,
    )
    _m3u_body(lines, context, playlist, problems)
    return playlist, problems


def _m3u_lines(data: bytes, problems: list[Problem]) -> list[str] | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        problems.append(Problem("line 1", "m3u:encoding", f"not UTF-8 (offset {exc.start})"))
        return None
    if text.startswith("\ufeff"):
        problems.append(Problem("line 1", "m3u:encoding", "starts with a byte order mark"))
        text = text[1:]
    if "\r" in text:
        line = text[: text.index("\r")].count("\n") + 1
        problems.append(Problem(f"line {line}", "m3u:line-endings", "CR found; use LF only"))
        text = text.replace("\r", "")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _m3u_header(line: str, problems: list[Problem]) -> str:
    """Check the #EXTM3U line and return its url-tvg."""
    header = _M3U_HEADER.fullmatch(line)
    if header is None:
        problems.append(Problem("line 1", "m3u:header", 'attributes must be name="value" pairs'))
        return ""
    attributes = _attributes(header.group(1), "line 1", problems)
    epg_url = attributes.get("url-tvg", "")
    epg = urlsplit(epg_url)
    if not epg_url:
        problems.append(Problem("line 1", "m3u:url-tvg", "the header needs url-tvg"))
    elif (
        epg.scheme not in ("http", "https")
        or not epg.netloc
        or epg.path != "/xmltv.php"
        or _guide_credentials(epg_url) is None
    ):
        problems.append(
            Problem(
                "line 1",
                "m3u:url-tvg",
                "must be an absolute /xmltv.php URL with username and password",
            )
        )
    if "x-tvg-url" in attributes and attributes["x-tvg-url"] != epg_url:
        problems.append(Problem("line 1", "m3u:url-tvg", "x-tvg-url must equal url-tvg"))
    return epg_url


def _guide_credentials(epg_url: str) -> tuple[str, str] | None:
    query = parse_qs(urlsplit(epg_url).query)
    if {"username", "password"} <= query.keys():
        return query["username"][0], query["password"][0]
    return None


def _m3u_body(
    lines: list[str], context: _PlaylistContext, playlist: Playlist, problems: list[Problem]
) -> None:
    seen: dict[str, int] = {}
    pending: _Extinf | None = None
    for number, line in enumerate(lines[1:], start=2):
        where = f"line {number}"
        if line.startswith("#EXTINF"):
            if pending is not None:
                problems.append(
                    Problem(
                        f"line {pending.line}",
                        "m3u:missing-url",
                        "#EXTINF must be followed by its URL line",
                    )
                )
            pending = _m3u_extinf(line, number, problems)
        elif line == "":
            problems.append(Problem(where, "m3u:blank-line", "blank lines are not allowed"))
        elif line.startswith("#"):
            problems.append(
                Problem(where, "m3u:directive", f"unexpected directive {line.split(':')[0]!r}")
            )
        elif pending is None:
            problems.append(Problem(where, "m3u:orphan-url", "URL line without an #EXTINF"))
        else:
            entry = _m3u_entry(pending, line, number, context, problems)
            pending = None
            if entry is not None:
                playlist.entries.append(entry)
                if line in seen:
                    problems.append(
                        Problem(where, "m3u:duplicate", f"same URL as line {seen[line]}")
                    )
                seen.setdefault(line, number)
    if pending is not None:
        problems.append(
            Problem(
                f"line {pending.line}",
                "m3u:missing-url",
                "#EXTINF must be followed by its URL line",
            )
        )


def _m3u_extinf(line: str, number: int, problems: list[Problem]) -> _Extinf | None:
    where = f"line {number}"
    match = _M3U_EXTINF.fullmatch(line)
    if match is None:
        problems.append(
            Problem(
                where,
                "m3u:extinf",
                '#EXTINF:-1 name="value" ...,Display name '
                "expected; quotes are not allowed in values",
            )
        )
        return None
    extinf = _Extinf(
        number, match.group(1), _attributes(match.group(2), where, problems), match.group(3)
    )
    if extinf.duration != "-1":
        problems.append(Problem(where, "m3u:duration", f"found {extinf.duration}, expected -1"))
    missing = [key for key in _M3U_ATTRIBUTES if key not in extinf.attributes]
    unexpected = [key for key in extinf.attributes if key not in _M3U_ATTRIBUTES]
    if missing or unexpected:
        problems.append(
            Problem(
                where,
                "m3u:attributes",
                f"missing {missing}, unexpected {unexpected}; exactly "
                f"{', '.join(_M3U_ATTRIBUTES)} are written",
            )
        )
    if not extinf.name.strip() or extinf.name != extinf.name.strip():
        problems.append(
            Problem(where, "m3u:name", "the display name must be non-empty and trimmed")
        )
    if extinf.attributes.get("tvg-name", extinf.name) != extinf.name:
        problems.append(Problem(where, "m3u:name", "tvg-name must equal the display name"))
    if not extinf.attributes.get("group-title", "-").strip():
        problems.append(Problem(where, "m3u:group-title", "group-title must name the category"))
    logo = extinf.attributes.get("tvg-logo", "")
    if logo and not IMAGE_URL.fullmatch(logo):
        problems.append(
            Problem(where, "m3u:tvg-logo", 'must be "" or an absolute JPEG/PNG/WebP URL')
        )
    return extinf


def _m3u_entry(
    extinf: _Extinf, url: str, url_line: int, context: _PlaylistContext, problems: list[Problem]
) -> PlaylistEntry | None:
    where, url_where = f"line {extinf.line}", f"line {url_line}"
    parts = urlsplit(url)
    path = _PLAY_PATH.fullmatch(parts.path)
    if (
        path is None
        or parts.scheme not in ("http", "https")
        or not parts.netloc
        or (parts.query or parts.fragment)
    ):
        problems.append(
            Problem(
                url_where,
                "m3u:url",
                "expected an absolute /{live|movie|series}/{user}/{pass}/{id}.{ext} URL",
            )
        )
        return None
    kind, ext = path.group(1), path.group(5)
    expected_ext = context.live_ext if kind == "live" else "mp4"
    if ext != expected_ext:
        problems.append(
            Problem(url_where, "m3u:url", f"{kind} URLs end in .{expected_ext} here, found .{ext}")
        )
    if context.origin and _origin(url) != context.origin:
        problems.append(Problem(url_where, "m3u:origin", f"URLs must be on {context.origin}"))
    if context.credentials and (unquote(path.group(2)), unquote(path.group(3))) != (
        context.credentials
    ):
        problems.append(
            Problem(
                url_where,
                "m3u:credentials",
                "the URL must carry the credentials the playlist was fetched with",
            )
        )
    tvg_id = extinf.attributes.get("tvg-id", "")
    if kind != "live" and tvg_id:
        problems.append(Problem(where, "m3u:tvg-id", 'movies and episodes have tvg-id ""'))
    if kind == "live" and re.search(r"\s", tvg_id):
        problems.append(Problem(where, "m3u:tvg-id", "tvg-id must not contain whitespace"))
    if kind == "series" and not _EPISODE_NAME.fullmatch(extinf.name):
        problems.append(
            Problem(
                where,
                "m3u:episode-name",
                f'found {extinf.name!r}; episodes are named "<Series> S01E02"',
            )
        )
    return PlaylistEntry(extinf.line, kind, int(path.group(4)), extinf.name, extinf.attributes, url)


def check_playlist_against_catalog(playlist: Playlist, catalog: Catalog) -> list[Problem]:
    """A playlist lists only what the account may see through player_api.php."""
    known: dict[str, set[int] | None] = {
        "movie": _ids(catalog.payloads.get("get_vod_streams"), "stream_id"),
        "live": _ids(catalog.payloads.get("get_live_streams"), "stream_id"),
        "series": (
            {
                int(ep["id"])
                for info in catalog.series_infos
                for eps in info["episodes"].values()
                for ep in eps
            }
            if catalog.series_infos
            and len(catalog.series_infos) == len(catalog.payloads.get("get_series") or [])
            else None
        ),
    }
    problems = []
    for entry in playlist.entries:
        allowed = known[entry.kind]
        if allowed is not None and entry.xc_id not in allowed:
            problems.append(
                Problem(
                    f"line {entry.line}",
                    "m3u:catalog",
                    f"{entry.kind} {entry.xc_id} is not in the account's catalog",
                )
            )
    return problems


def _ids(items: Any, key: str) -> set[int] | None:
    return None if items is None else {int(item[key]) for item in items}


# --------------------------------------------------------------------------- XMLTV


_XMLTV_TIME = re.compile(r"[0-9]{14} \+0000")


@dataclass
class Guide:
    channels: dict[str, list[str]] = field(default_factory=dict)
    programmes: int = 0


def check_xmltv(data: bytes) -> tuple[Guide, list[Problem]]:
    """Check an xmltv.php guide (compat/xmltv.md)."""
    guide, problems = Guide(), []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return guide, [Problem("document", "xmltv:encoding", f"not UTF-8 (offset {exc.start})")]
    if "<!ENTITY" in text or re.search(r"<!DOCTYPE[^>]*\[", text):
        return guide, [
            Problem(
                "document",
                "xmltv:doctype",
                "declares entities or an internal DTD subset; XMLTV needs neither",
            )
        ]
    declaration = re.match(r"<\?xml[^>]*encoding=[\"']([^\"']+)[\"']", text)
    if declaration and declaration.group(1).lower() != "utf-8":
        problems.append(Problem("document", "xmltv:encoding", "the declaration must say UTF-8"))
    try:
        # Entities and internal subsets are rejected above; expat never fetches external DTDs.
        root = ET.fromstring(data)  # noqa: S314
    except ET.ParseError as exc:
        line, column = exc.position
        return guide, [Problem(f"line {line}, column {column}", "xmltv:well-formed", str(exc))]
    if root.tag != "tv":
        return guide, [Problem(f"/{root.tag}", "xmltv:root", "the root element must be <tv>")]
    channel_count = 0
    for child in root:
        if child.tag == "channel":
            channel_count += 1
            where = f"/tv/channel[{channel_count}]"
            if guide.programmes:
                problems.append(Problem(where, "xmltv:order", "channels come before programmes"))
            problems += _check_channel(child, where, guide)
        elif child.tag == "programme":
            guide.programmes += 1
            problems += _check_programme(child, f"/tv/programme[{guide.programmes}]", guide)
        else:
            problems.append(
                Problem(
                    f"/tv/{child.tag}",
                    "xmltv:element",
                    "only <channel> and <programme> may appear under <tv>",
                )
            )
    return guide, problems


def _check_channel(element: ET.Element, where: str, guide: Guide) -> list[Problem]:
    problems = []
    channel_id = element.get("id", "")
    if not channel_id or re.search(r"\s", channel_id):
        problems.append(
            Problem(f"{where}/@id", "xmltv:channel", "needs a non-empty id without whitespace")
        )
    elif channel_id in guide.channels:
        problems.append(
            Problem(f"{where}/@id", "xmltv:channel", f"duplicate channel id {channel_id!r}")
        )
    names = [(name.text or "").strip() for name in element.findall("display-name")]
    if not names or not all(names):
        problems.append(
            Problem(where, "xmltv:channel", "needs at least one non-empty display-name")
        )
    for icon in element.findall("icon"):
        if not re.fullmatch(r"https?://\S+", icon.get("src", "")):
            problems.append(
                Problem(f"{where}/icon/@src", "xmltv:icon", "must be an absolute http(s) URL")
            )
    guide.channels.setdefault(channel_id, names)
    return problems


def _check_programme(element: ET.Element, where: str, guide: Guide) -> list[Problem]:
    problems = []
    times = {}
    for key in ("start", "stop"):
        value = element.get(key, "")
        if not _XMLTV_TIME.fullmatch(value):
            problems.append(
                Problem(
                    f"{where}/@{key}",
                    "xmltv:time",
                    f"found {value!r}, expected YYYYMMDDhhmmss +0000 (UTC)",
                )
            )
            continue
        try:
            times[key] = datetime.strptime(value[:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            problems.append(
                Problem(f"{where}/@{key}", "xmltv:time", f"{value!r} is not a real time")
            )
    if len(times) == 2 and times["stop"] <= times["start"]:
        problems.append(Problem(f"{where}/@stop", "xmltv:time", "must be later than start"))
    if element.get("channel") not in guide.channels:
        problems.append(
            Problem(
                f"{where}/@channel",
                "xmltv:channel-ref",
                f"{element.get('channel')!r} is not a declared channel",
            )
        )
    titles = [(title.text or "").strip() for title in element.findall("title")]
    if not titles or not all(titles):
        problems.append(Problem(where, "xmltv:title", "needs at least one non-empty title"))
    return problems


def check_guide_against_catalog(guide: Guide, catalog: Catalog) -> list[Problem]:
    """The guide covers only the account's channels."""
    streams = catalog.payloads.get("get_live_streams")
    if streams is None:
        return []
    known = {item["epg_channel_id"] for item in streams if item["epg_channel_id"]}
    return [
        Problem(
            f"/tv/channel[@id={channel_id!r}]",
            "xmltv:catalog",
            "is not the epg_channel_id of any of the account's live streams",
        )
        for channel_id in guide.channels
        if channel_id not in known
    ]


# --------------------------------------------------------------------------- fixtures


@dataclass(frozen=True)
class GoldenSet:
    """The golden fixtures, parsed, and the account context they describe."""

    raw: dict[str, bytes]
    payloads: dict[str, Any]
    origin: str
    credentials: tuple[str, str]

    def catalog(self, replace: tuple[str, Any] | None = None) -> Catalog:
        payloads = dict(self.payloads)
        if replace is not None:
            payloads[replace[0]] = replace[1]
        return Catalog(payloads=payloads, series_infos=[payloads["get_series_info"]])


def _server_origin(server_info: dict[str, Any]) -> str:
    scheme = server_info["server_protocol"]
    port = server_info["https_port"] if scheme == "https" else server_info["port"]
    default = {"http": "80", "https": "443"}[scheme]
    return f"{scheme}://{server_info['url']}" + ("" if port == default else f":{port}")


def load_golden(suite: SchemaSuite, reporter: Reporter) -> GoldenSet | None:
    """Check every golden fixture; return them when all pass (cases are built from them)."""
    raw: dict[str, bytes] = {}
    payloads: dict[str, Any] = {}
    all_passed = True
    for action in ACTIONS:
        name = f"{action}.json"
        path = FIXTURE_DIR / name
        if not path.is_file():
            reporter.fail(name, [Problem(f"fixtures/{name}", "fixture", "missing")])
            all_passed = False
            continue
        raw[name] = path.read_bytes()
        payload, problems = parse_json(raw[name])
        if payload is not None:
            problems = suite.check(action, payload)
            payloads[action] = payload
        all_passed &= reporter.result(name, problems, suite.titles[action])
    for name in (*M3U_FIXTURES, *XMLTV_FIXTURES):
        path = FIXTURE_DIR / name
        if path.is_file():
            raw[name] = path.read_bytes()
        else:
            reporter.fail(name, [Problem(f"fixtures/{name}", "fixture", "missing")])
            all_passed = False
    if not all_passed:
        return None
    login = payloads["login"]["user_info"]
    golden = GoldenSet(
        raw,
        payloads,
        _server_origin(payloads["login"]["server_info"]),
        (login["username"], login["password"]),
    )
    all_passed &= reporter.result(
        "fixtures agree with each other",
        check_catalog(golden.catalog()),
        "categories visible, stream ids unique across kinds",
    )
    for name, live_ext in M3U_FIXTURES.items():
        all_passed &= reporter.result(
            name,
            check_document(suite, golden, name, raw[name]),
            f"get.php?type=m3u_plus&output={live_ext}",
        )
    for name in XMLTV_FIXTURES:
        all_passed &= reporter.result(
            name, check_document(suite, golden, name, raw[name]), "xmltv.php"
        )
    return golden if all_passed else None


def check_document(suite: SchemaSuite, golden: GoldenSet, name: str, data: bytes) -> list[Problem]:
    """Check one fixture-shaped document in the context of the golden set."""
    if name in M3U_FIXTURES:
        playlist, problems = check_m3u(
            data, live_ext=M3U_FIXTURES[name], origin=golden.origin, credentials=golden.credentials
        )
        return problems + check_playlist_against_catalog(playlist, golden.catalog())
    if name in XMLTV_FIXTURES:
        guide, problems = check_xmltv(data)
        return problems + check_guide_against_catalog(guide, golden.catalog())
    action = name.removesuffix(".json")
    payload, problems = parse_json(data)
    if payload is None:
        return problems
    problems = suite.check(action, payload)
    if not problems:
        problems = check_catalog(golden.catalog(replace=(action, payload)))
    return problems


def _pointer_parts(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise SuiteError(f"JSON Pointer {pointer!r} must start with /")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def apply_patch(document: Any, operations: list[Any]) -> Any:
    """RFC 6902 add, remove and replace: enough to break one thing in a golden fixture."""
    document = copy.deepcopy(document)
    for operation in operations:
        op, parts = operation["op"], _pointer_parts(operation["path"])
        if op not in ("add", "remove", "replace"):
            raise SuiteError(f"unsupported patch op {op!r}")
        if not parts:
            if op != "replace":
                raise SuiteError("only replace can target the whole document")
            document = copy.deepcopy(operation["value"])
            continue
        parent = document
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        key: int | str = parts[-1]
        if isinstance(parent, list):
            key = len(parent) if key == "-" else int(key)
        elif op == "replace" and key not in parent:
            raise SuiteError(f"replace target {operation['path']} does not exist")
        if op == "remove":
            del parent[key]
        elif op == "add" and isinstance(parent, list):
            parent.insert(int(key), copy.deepcopy(operation["value"]))
        else:
            parent[key] = copy.deepcopy(operation["value"])
    return document


@dataclass(frozen=True)
class InvalidCase:
    name: str
    description: str
    fixture: str
    data: bytes
    expect_at: str
    expect_kind: str


_CASE_KEYS = {"description", "fixture", "patch", "replace", "expect"}


def load_case(path: Path, golden: GoldenSet) -> InvalidCase:
    case, problems = parse_json(path.read_bytes())
    if problems or not isinstance(case, dict):
        raise SuiteError(f"{path.name}: not a JSON object")
    unknown = case.keys() - _CASE_KEYS
    fixture = case.get("fixture")
    expect = case.get("expect")
    if (
        unknown
        or not isinstance(case.get("description"), str)
        or fixture not in golden.raw
        or not isinstance(expect, dict)
        or set(expect) != {"at", "kind"}
        or ("patch" in case) == ("replace" in case)
    ):
        raise SuiteError(
            f"{path.name}: needs description, fixture (a golden file), expect "
            "{at, kind} and exactly one of patch or replace"
        )
    try:
        if "patch" in case:
            if not fixture.endswith(".json"):
                raise SuiteError("patch works on JSON fixtures; use replace for text")
            golden_payload, _ = parse_json(golden.raw[fixture])
            patched = apply_patch(golden_payload, case["patch"])
            data = (json.dumps(patched, ensure_ascii=False, indent=2) + "\n").encode()
        else:
            text = golden.raw[fixture].decode("utf-8")
            for edit in case["replace"]:
                if text.count(edit["old"]) != 1:
                    raise SuiteError(f"replace text {edit['old']!r} must occur exactly once")
                text = text.replace(edit["old"], edit["new"])
            data = text.encode("utf-8")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise SuiteError(f"{path.name}: cannot apply the change: {exc!r}") from exc
    except SuiteError as exc:
        raise SuiteError(f"{path.name}: {exc}") from exc
    return InvalidCase(path.stem, case["description"], fixture, data, expect["at"], expect["kind"])


def run_fixture_checks(suite: SchemaSuite, reporter: Reporter) -> None:
    reporter.section("Golden fixtures (must pass)")
    golden = load_golden(suite, reporter)
    if golden is None:
        reporter.write("  Invalid cases skipped: the golden fixtures must pass first.")
        return
    reporter.section("Invalid cases (must fail at the expected place)")
    paths = sorted(INVALID_DIR.glob("*.json"))
    if not paths:
        reporter.fail("fixtures/invalid", [Problem("fixtures/invalid", "fixture", "no cases")])
    for path in paths:
        try:
            case = load_case(path, golden)
        except SuiteError as exc:
            reporter.fail(path.stem, [Problem(f"fixtures/invalid/{path.name}", "case", str(exc))])
            continue
        problems = check_document(suite, golden, case.fixture, case.data)
        matched = [p for p in problems if p.where == case.expect_at and p.kind == case.expect_kind]
        if matched:
            reporter.ok(case.name)
            if reporter.verbose:
                for problem in problems:
                    reporter.write(f"        {problem}")
            continue
        expected = Problem(case.expect_at, case.expect_kind, "expected failure")
        reporter.fail(
            case.name,
            [expected, *problems]
            if problems
            else [
                expected,
                Problem(case.fixture, "case", "the changed document passed every check"),
            ],
        )


# --------------------------------------------------------------------------- live


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


class _HTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, *, timeout: float, connect_host: str) -> None:
        super().__init__(host, port, timeout=timeout)
        self._connect_host = connect_host

    def connect(self) -> None:
        self.sock = socket.create_connection((self._connect_host, self.port), self.timeout)


class _HTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self, host: str, port: int, *, timeout: float, connect_host: str, context: ssl.SSLContext
    ) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._connect_host = connect_host
        self._tls = context

    def connect(self) -> None:
        sock = socket.create_connection((self._connect_host, self.port), self.timeout)
        self.sock = self._tls.wrap_socket(sock, server_hostname=self.host)


class HttpClient:
    """Plain requests to the Xtream host: never follows redirects, decodes gzip.

    *.localhost names connect to 127.0.0.1 (RFC 6761, as browsers and curl do) while
    the Host header and TLS name stay the configured host.
    """

    def __init__(self, base_url: str, timeout: float) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise SuiteError(f"--live needs an http(s) URL, got {base_url!r}")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise SuiteError("--live takes the server URL only, without credentials or a query")
        self.scheme, self.host = parts.scheme, parts.hostname
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.prefix = parts.path.rstrip("/")
        default = 443 if self.scheme == "https" else 80
        self.origin = f"{self.scheme}://{self.host}" + (
            "" if self.port == default else f":{self.port}"
        )
        loopback = self.host == "localhost" or self.host.endswith(".localhost")
        self._connect_host = "127.0.0.1" if loopback else self.host
        self._timeout = timeout
        self._tls = ssl.create_default_context() if self.scheme == "https" else None

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
    ) -> Response:
        target = self.prefix + path + (f"?{urlencode(query)}" if query else "")
        body = urlencode(form).encode() if form is not None else None
        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT,
            "Connection": "close",
        }
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        connection: http.client.HTTPConnection
        if self._tls is not None:
            connection = _HTTPSConnection(
                self.host,
                self.port,
                timeout=self._timeout,
                connect_host=self._connect_host,
                context=self._tls,
            )
        else:
            connection = _HTTPConnection(
                self.host, self.port, timeout=self._timeout, connect_host=self._connect_host
            )
        try:
            connection.request(method, target, body=body, headers=headers)
            raw = connection.getresponse()
            data = raw.read()
            response_headers = {key.lower(): value for key, value in raw.getheaders()}
            status = raw.status
        except (OSError, http.client.HTTPException) as exc:
            raise LiveError(f"{method} {path}: {type(exc).__name__}: {exc}") from exc
        finally:
            connection.close()
        encoding = response_headers.get("content-encoding", "").lower()
        if encoding == "gzip":
            try:
                data = gzip.decompress(data)
            except (OSError, EOFError) as exc:
                raise LiveError(f"{method} {path}: broken gzip body: {exc}") from exc
        elif encoding not in ("", "identity"):
            raise LiveError(f"{method} {path}: unrequested Content-Encoding {encoding!r}")
        return Response(status, response_headers, data)


class LiveRun:
    """The live checks, in order; auth failures run last because they trip rate limits."""

    def __init__(
        self,
        suite: SchemaSuite,
        reporter: Reporter,
        client: HttpClient,
        credentials: tuple[str, str],
        *,
        play: bool,
    ) -> None:
        self.suite, self.reporter, self.client = suite, reporter, client
        self.username, self.password = credentials
        self.play = play
        self.catalog = Catalog()
        self.server_origin = ""

    # -- helpers

    def _fetch(self, label: str, method: str, path: str, fields: dict[str, str]) -> Response | None:
        try:
            if method == "GET":
                return self.client.request("GET", path, query=fields)
            return self.client.request("POST", path, form=fields)
        except LiveError as exc:
            self.reporter.fail(label, [Problem("HTTP", "http:connection", str(exc))])
            return None

    def _http_problems(self, response: Response, content_types: tuple[str, ...]) -> list[Problem]:
        problems = []
        if response.status != 200:
            location = response.headers.get("location", "")
            problems.append(
                Problem(
                    "HTTP",
                    "http:status",
                    f"status {response.status}, expected 200"
                    + (f" (Location {location})" if location else ""),
                )
            )
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in content_types:
            problems.append(
                Problem(
                    "HTTP",
                    "http:content-type",
                    f"Content-Type {content_type!r}, expected " + " or ".join(content_types),
                )
            )
        return problems

    def api(
        self,
        schema: str,
        params: dict[str, str],
        *,
        method: str = "GET",
        credentials: tuple[str, str] | None = None,
        label: str | None = None,
    ) -> tuple[Any, bytes | None]:
        """Call player_api.php, report it, and return (payload if it passed, raw body)."""
        user, password = credentials or (self.username, self.password)
        shown = " ".join(f"{key}={value}" for key, value in params.items())
        label = label or f"{method} player_api.php {shown}".rstrip()
        response = self._fetch(
            label, method, "/player_api.php", {"username": user, "password": password, **params}
        )
        if response is None:
            return None, None
        problems = self._http_problems(response, ("application/json",))
        payload, json_problems = parse_json(response.body)
        problems += json_problems
        if payload is not None:
            if schema != "auth_failure" and payload == {"user_info": {"auth": 0}}:
                problems.append(
                    Problem(
                        "$", "auth", "authentication failed (auth 0); check XC_USER and XC_PASS"
                    )
                )
            else:
                problems += self.suite.check(schema, payload)
        passed = self.reporter.result(label, problems, schema)
        return (payload if passed else None), response.body

    def check(self, label: str, problems: list[Problem], detail: str = "") -> None:
        self.reporter.result(label, problems, detail)

    # -- the run

    def run(self) -> None:
        self.reporter.section(f"Live checks against {self.client.origin}")
        login, _ = self.api("login", {}, label="GET player_api.php (login)")
        if login is None:
            self.reporter.write("  Remaining live checks skipped: the login must pass first.")
            return
        self._login_details(login)
        self.api("login", {}, method="POST", label="POST player_api.php (login, form body)")
        self.api("login", {"action": "get_account_info"})
        self.api(
            "login",
            {"action": "compat_unknown_action"},
            label="GET player_api.php action=<unknown> (login payload)",
        )

        vod_categories, _ = self.api(
            "get_vod_categories", {"action": "get_vod_categories"}, method="POST"
        )
        series_categories, _ = self.api(
            "get_series_categories", {"action": "get_series_categories"}
        )
        live_categories, _ = self.api("get_live_categories", {"action": "get_live_categories"})
        for action, payload in (
            ("get_vod_categories", vod_categories),
            ("get_series_categories", series_categories),
            ("get_live_categories", live_categories),
        ):
            if payload is not None:
                self.catalog.payloads[action] = payload

        vod = self._list("get_vod_streams", vod_categories)
        if vod:
            self._vod_info(vod[0])
        else:
            self.reporter.skip(
                "get_vod_info",
                "the account has no movies" if vod is not None else "get_vod_streams failed",
            )
        series = self._list("get_series", series_categories)
        if series:
            self._series_info(series[0])
        else:
            self.reporter.skip(
                "get_series_info",
                "the account has no series" if series is not None else "get_series failed",
            )
        live = self._list("get_live_streams", live_categories)
        self._epg(live)

        if len(self.catalog.payloads) > 1:
            self.check(
                "responses agree with each other",
                check_catalog(self.catalog),
                "categories visible, stream ids unique across kinds",
            )
        for output, live_ext in PLAYLIST_OUTPUTS.items():
            self._playlist(output, live_ext)
        self._guide()
        if self.play:
            self._play(vod)
        self._auth_failures()

    def _login_details(self, login: Any) -> None:
        info, server = login["user_info"], login["server_info"]
        problems = []
        if info["username"] != self.username:
            problems.append(
                Problem("$.user_info.username", "echo", "differs from the username that was sent")
            )
        if info["password"] != self.password:
            problems.append(
                Problem(
                    "$.user_info.password",
                    "echo",
                    "differs from the password that was sent; apps expect it echoed",
                )
            )
        self.server_origin = _server_origin(server)
        if self.server_origin != self.client.origin:
            problems.append(
                Problem(
                    "$.server_info",
                    "server-origin",
                    f"describes {self.server_origin}, but the server was reached at "
                    f"{self.client.origin}; apps build play URLs from server_info",
                )
            )
        self.check("login echoes the credentials and describes this server", problems)

    def _list(self, action: str, categories: Any) -> list[Any] | None:
        items, _ = self.api(action, {"action": action})
        if items is None:
            self.reporter.skip(f"{action} category_id=", f"{action} failed")
            return None
        self.catalog.payloads[action] = items
        if not categories:
            self.reporter.skip(
                f"{action} category_id=", "the account has no categories of this kind"
            )
            return list(items)
        category_id = categories[0]["category_id"]
        filtered, _ = self.api(action, {"action": action, "category_id": category_id})
        if filtered is not None:
            self.check(
                f"{action} category_id={category_id} lists only that category",
                [
                    Problem(
                        f"$[{index}].category_ids",
                        "category-filter",
                        f"the item is not in category {category_id}",
                    )
                    for index, item in enumerate(filtered)
                    if int(category_id) not in item["category_ids"]
                ],
            )
        unknown = str(max(int(c["category_id"]) for c in categories) + 1_000_000)
        empty, _ = self.api(
            action,
            {"action": action, "category_id": unknown},
            label=f"GET player_api.php action={action} category_id=<unknown>",
        )
        if empty is not None:
            self.check(
                f"{action} for an unknown category is []",
                []
                if empty == []
                else [Problem("$", "category-filter", f"found {describe(empty)}, expected []")],
            )
        return list(items)

    def _vod_info(self, stream: Any) -> None:
        vod_id = str(stream["stream_id"])
        info, _ = self.api("get_vod_info", {"action": "get_vod_info", "vod_id": vod_id})
        if info is None:
            return
        self.catalog.payloads["get_vod_info"] = info
        problems = []
        if info["movie_data"]["stream_id"] != stream["stream_id"]:
            problems.append(Problem("$.movie_data.stream_id", "lookup", f"expected {vod_id}"))
        if info["movie_data"]["name"] != stream["name"]:
            problems.append(
                Problem("$.movie_data.name", "lookup", "differs from the name in get_vod_streams")
            )
        self.check(f"get_vod_info vod_id={vod_id} describes that movie", problems)

    def _series_info(self, item: Any) -> None:
        series_id = str(item["series_id"])
        info, _ = self.api("get_series_info", {"action": "get_series_info", "series_id": series_id})
        if info is None:
            return
        self.catalog.series_infos.append(info)
        problems = []
        if info["info"]["name"] != item["name"]:
            problems.append(Problem("$.info.name", "lookup", "differs from the name in get_series"))
        self.check(f"get_series_info series_id={series_id} describes that series", problems)

    def _epg(self, live: list[Any] | None) -> None:
        if live is None:
            for action in ("get_short_epg", "get_simple_data_table"):
                self.reporter.skip(action, "get_live_streams failed")
            return
        channel = next((item for item in live if item["epg_channel_id"]), None)
        if channel is None:
            for action in ("get_short_epg", "get_simple_data_table"):
                self.reporter.skip(action, "no live stream has an epg_channel_id yet (M12)")
            return
        stream_id = str(channel["stream_id"])
        short, _ = self.api(
            "get_short_epg", {"action": "get_short_epg", "stream_id": stream_id, "limit": "4"}
        )
        full, _ = self.api(
            "get_simple_data_table", {"action": "get_simple_data_table", "stream_id": stream_id}
        )
        problems = []
        if short is not None and len(short["epg_listings"]) > 4:
            problems.append(
                Problem(
                    "$.epg_listings",
                    "epg-limit",
                    f"{len(short['epg_listings'])} listings for limit=4",
                )
            )
        for name, payload in (("get_short_epg", short), ("get_simple_data_table", full)):
            for index, item in enumerate((payload or {}).get("epg_listings", [])):
                if item["channel_id"] != channel["epg_channel_id"]:
                    problems.append(
                        Problem(
                            f"{name} $.epg_listings[{index}].channel_id",
                            "epg-channel",
                            "differs from the stream's epg_channel_id",
                        )
                    )
        self.check(f"EPG of stream {stream_id} belongs to it and honours limit", problems)

    def _playlist(self, output: str, live_ext: str) -> None:
        label = f"GET get.php type=m3u_plus output={output}"
        response = self._fetch(
            label,
            "GET",
            "/get.php",
            {
                "username": self.username,
                "password": self.password,
                "type": "m3u_plus",
                "output": output,
            },
        )
        if response is None:
            return
        problems = self._http_problems(response, ("audio/x-mpegurl",))
        playlist, playlist_problems = check_m3u(
            response.body,
            live_ext=live_ext,
            origin=self.server_origin or None,
            credentials=(self.username, self.password),
        )
        problems += playlist_problems + check_playlist_against_catalog(playlist, self.catalog)
        counts = {
            kind: sum(entry.kind == kind for entry in playlist.entries)
            for kind in ("live", "movie", "series")
        }
        self.check(label, problems, ", ".join(f"{n} {kind}" for kind, n in counts.items()))

    def _guide(self) -> None:
        label = "GET xmltv.php"
        response = self._fetch(
            label, "GET", "/xmltv.php", {"username": self.username, "password": self.password}
        )
        if response is None:
            return
        problems = self._http_problems(response, ("application/xml", "text/xml"))
        guide, guide_problems = check_xmltv(response.body)
        problems += guide_problems + check_guide_against_catalog(guide, self.catalog)
        self.check(
            label, problems, f"{len(guide.channels)} channels, {guide.programmes} programmes"
        )

    def _play(self, vod: list[Any] | None) -> None:
        targets = []
        if vod:
            targets.append(("movie", str(vod[0]["stream_id"]), vod[0]["container_extension"]))
        for info in self.catalog.series_infos:
            first: list[Any] = next(iter(info["episodes"].values()), [])
            if first:
                targets.append(("series", first[0]["id"], first[0]["container_extension"]))
        if not targets:
            self.reporter.skip("play redirects", "no movie or episode to play")
        for kind, xc_id, ext in targets:
            label = f"GET /{kind}/<user>/<pass>/{xc_id}.{ext} redirects to the edge"
            user, password = quote(self.username, safe=""), quote(self.password, safe="")
            path = f"/{kind}/{user}/{password}/{xc_id}.{ext}"
            try:
                response = self.client.request("GET", path)
            except LiveError as exc:
                self.reporter.fail(label, [Problem("HTTP", "http:connection", str(exc))])
                continue
            location = response.headers.get("location", "")
            problems = []
            if response.status != 302:
                problems.append(
                    Problem("HTTP", "play:status", f"status {response.status}, expected 302")
                )
            elif not re.fullmatch(r"https?://\S+", location):
                problems.append(
                    Problem("HTTP", "play:location", "Location must be an absolute URL")
                )
            else:
                if self.client.scheme == "https" and not location.startswith("https://"):
                    problems.append(
                        Problem("HTTP", "play:location", "https must redirect to https")
                    )
                if self.username in location or self.password in location:
                    problems.append(
                        Problem(
                            "HTTP", "play:location", "the edge URL must not carry the credentials"
                        )
                    )
            self.check(label, problems, f"to {_origin(location)}" if location else "")

    def _auth_failures(self) -> None:
        wrong = "compat-" + secrets.token_urlsafe(12)
        self.reporter.redact.add(wrong)
        attempts = [
            ("auth failure: wrong password", "GET", (self.username, wrong), {}),
            (
                "auth failure: unknown username",
                "GET",
                ("compat-" + secrets.token_hex(6), wrong),
                {},
            ),
            (
                "auth failure: wrong password on an action (POST)",
                "POST",
                (self.username, wrong),
                {"action": "get_vod_streams"},
            ),
        ]
        bodies = []
        for label, method, credentials, params in attempts:
            _, body = self.api(
                "auth_failure", params, method=method, credentials=credentials, label=label
            )
            if body is not None:
                bodies.append(body)
        if len(bodies) == len(attempts):
            self.check(
                "auth failures are byte-for-byte identical",
                []
                if len(set(bodies)) == 1
                else [
                    Problem(
                        "$", "auth", "the bodies differ, which reveals whether a username exists"
                    )
                ],
            )


# --------------------------------------------------------------------------- main


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the Xtream contract: schemas, golden fixtures, invalid cases and, "
        "with --live, a running server.",
        epilog="Credentials for --live come from the XC_USER and XC_PASS environment variables.",
    )
    parser.add_argument(
        "--live",
        metavar="BASE_URL",
        help="also check a running Xtream host at the URL apps use, e.g. http://tv.localhost:8080",
    )
    parser.add_argument(
        "--play",
        action="store_true",
        help="with --live, also request one movie and one episode play URL and "
        "check the 302; this holds a stream slot until the session expires",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per request (30)")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="print every problem an invalid case raises"
    )
    args = parser.parse_args(argv)
    if args.play and not args.live:
        parser.error("--play needs --live")

    redact = Redactor()
    reporter = Reporter(redact, verbose=args.verbose)
    try:
        suite = load_suite()
        reporter.write(
            f"Xtream contract suite: {len(ACTIONS)} JSON contracts, "
            f"{len(M3U_FIXTURES) + len(XMLTV_FIXTURES)} text formats"
        )
        run_fixture_checks(suite, reporter)
        if args.live:
            credentials = os.environ.get("XC_USER", ""), os.environ.get("XC_PASS", "")
            if not all(credentials):
                raise SuiteError("--live needs the XC_USER and XC_PASS environment variables")
            for secret in credentials:
                redact.add(secret)
            client = HttpClient(args.live, args.timeout)
            LiveRun(suite, reporter, client, credentials, play=args.play).run()
    except SuiteError as exc:
        sys.stderr.write(redact(f"validate.py: {exc}") + "\n")
        return 2
    reporter.write()
    verdict = "FAILED" if reporter.failed else "OK"
    reporter.write(
        f"{verdict}: {reporter.passed} passed, {reporter.failed} failed, {reporter.skipped} skipped"
    )
    return 1 if reporter.failed else 0


if __name__ == "__main__":
    sys.exit(main())
