"""The typed settings registry (SPEC §6 ops, §8.3 Settings).

Every runtime-tunable value (branding, token TTLs, thresholds, feature flags) is
declared here once with its type, default, constraints and group. The database
(`core.Setting`) stores admin overrides only. Read values with
`apps.core.services.get_setting`, change them with `set_setting`.
"""

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import EmailValidator, URLValidator

type SettingValue = bool | int | float | str


class SettingKind(StrEnum):
    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    STR = "str"


class UnknownSettingError(KeyError):
    """The key is not declared in the registry."""


@dataclass(frozen=True, slots=True)
class SettingDef:
    key: str
    kind: SettingKind
    default: SettingValue
    description: str
    group: str
    validator: Callable[[Any], None] | None = None
    sensitive: bool = False
    min_value: float | None = field(default=None, kw_only=True)
    max_value: float | None = field(default=None, kw_only=True)
    choices: tuple[str, ...] | None = field(default=None, kw_only=True)

    def clean(self, value: object) -> SettingValue:
        """Return `value` converted to this setting's type, or raise ValidationError."""
        cleaned = self._coerce(value)
        if isinstance(cleaned, int | float) and not isinstance(cleaned, bool):
            if self.min_value is not None and cleaned < self.min_value:
                msg = f"Must be at least {self.min_value:g}."
                raise ValidationError(msg, code="min_value")
            if self.max_value is not None and cleaned > self.max_value:
                msg = f"Must be at most {self.max_value:g}."
                raise ValidationError(msg, code="max_value")
        if self.choices is not None and cleaned not in self.choices:
            msg = f"Must be one of: {', '.join(self.choices)}."
            raise ValidationError(msg, code="invalid_choice")
        if self.validator is not None:
            self.validator(cleaned)
        return cleaned

    def _coerce(self, value: object) -> SettingValue:
        match self.kind:
            case SettingKind.BOOL if isinstance(value, bool):
                return value
            case SettingKind.INT if isinstance(value, int) and not isinstance(value, bool):
                return value
            case SettingKind.FLOAT if isinstance(value, int | float) and not isinstance(
                value, bool
            ):
                return float(value)
            case SettingKind.STR if isinstance(value, str):
                return value
        msg = f"Expected a value of type {self.kind.value}."
        raise ValidationError(msg, code="invalid_type")


# --- Validators -------------------------------------------------------------------

_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_COUNTRY = re.compile(r"[A-Z]{2}")


def _pattern(regex: re.Pattern[str], message: str) -> Callable[[Any], None]:
    def validate(value: Any) -> None:
        if not regex.fullmatch(value):
            raise ValidationError(message, code="invalid")

    return validate


def _optional(validator: Callable[[Any], None]) -> Callable[[Any], None]:
    """Accept an empty string (setting not configured yet), else delegate."""

    def validate(value: Any) -> None:
        if value != "":
            validator(value)

    return validate


_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def genre_category_map(value: Any) -> None:
    """A JSON object mapping TMDB genre ids to category slugs: {"28": "action", ...}."""
    message = 'Enter a JSON object of TMDB genre ids to category slugs, e.g. {"28": "action"}.'
    try:
        data = json.loads(value)
    except ValueError:
        raise ValidationError(message, code="invalid") from None
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and key.isdigit() and isinstance(slug, str) and _SLUG.fullmatch(slug)
        for key, slug in data.items()
    ):
        raise ValidationError(message, code="invalid")


hex_color = _pattern(_HEX_COLOR, "Enter a colour as #RRGGBB.")
currency_code = _pattern(_CURRENCY, "Enter an ISO 4217 currency code, e.g. SAR.")
country_code = _pattern(_COUNTRY, "Enter an ISO 3166-1 alpha-2 country code, e.g. SA.")
optional_email = _optional(EmailValidator())
optional_http_url = _optional(URLValidator(schemes=["http", "https"]))


# --- The registry -------------------------------------------------------------------

GROUPS: tuple[str, ...] = (
    "branding",
    "xtream",
    "playback",
    "library",
    "metadata",
    "billing",
    "trials",
    "security",
    "features",
)

_K = SettingKind

#: TMDB movie and TV genre ids -> category slugs (SPEC §7.2 step 5). Categories are
#: created on first use; `apps.catalog.services.CATEGORY_NAMES` names the known slugs.
DEFAULT_GENRE_CATEGORY_MAP: Mapping[str, str] = MappingProxyType(
    {
        "28": "action", "12": "adventure", "16": "animation", "35": "comedy", "80": "crime",
        "99": "documentary", "18": "drama", "10751": "family", "14": "fantasy",
        "36": "history", "27": "horror", "10402": "music", "9648": "mystery",
        "10749": "romance", "878": "sci-fi", "53": "thriller", "10752": "war",
        "37": "western", "10759": "action", "10762": "kids", "10763": "news",
        "10764": "reality", "10765": "sci-fi", "10766": "drama", "10767": "talk",
        "10768": "war",
    }
)  # fmt: skip

_DEFINITIONS: tuple[SettingDef, ...] = (
    # Branding
    SettingDef("branding.service_name_en", _K.STR, "Smart IPTV", "Service name in English.",
               "branding"),
    SettingDef("branding.service_name_ar", _K.STR, "سمارت IPTV", "Service name in Arabic.",
               "branding"),
    SettingDef("branding.accent_color", _K.STR, "#0F766E", "Accent colour (#RRGGBB).",
               "branding", hex_color),
    SettingDef("branding.support_email", _K.STR, "", "Support contact email.", "branding",
               optional_email),
    # Xtream server_info (SPEC §7.5)
    SettingDef("xtream.server_url", _K.STR, "",
               "Public base URL IPTV apps use as the server, e.g. https://tv.example.com.",
               "xtream", optional_http_url),
    SettingDef("xtream.port", _K.INT, 80, "HTTP port reported to IPTV apps.", "xtream",
               min_value=1, max_value=65535),
    SettingDef("xtream.https_port", _K.INT, 443, "HTTPS port reported to IPTV apps.", "xtream",
               min_value=1, max_value=65535),
    # Playback (SPEC §7.4)
    SettingDef("playback.token_ttl_vod_s", _K.INT, 7200,
               "Lifetime of signed media URLs for movies and series, in seconds.", "playback",
               min_value=60, max_value=86400),
    SettingDef("playback.token_ttl_live_s", _K.INT, 21600,
               "Lifetime of signed media URLs for live channels, in seconds.", "playback",
               min_value=60, max_value=86400),
    SettingDef("playback.ip_binding", _K.BOOL, False,
               "Bind signed media URLs to the client's IP address.", "playback"),
    SettingDef("playback.realtime_transcode_enabled", _K.BOOL, False,
               "Allow real-time transcoding as a capped fallback.", "playback"),
    SettingDef("playback.realtime_transcode_max", _K.INT, 2,
               "Maximum concurrent real-time transcodes.", "playback",
               min_value=0, max_value=64),
    # Library scanning (SPEC §7.1)
    SettingDef("library.watcher_stable_s", _K.INT, 60,
               "A new file is scanned once its size has not changed for this many seconds.",
               "library", min_value=5, max_value=3600),
    # Metadata matching (SPEC §7.2)
    SettingDef("metadata.match_auto_accept", _K.FLOAT, 0.85,
               "Minimum confidence to accept a metadata match automatically.", "metadata",
               min_value=0.0, max_value=1.0),
    SettingDef("metadata.match_margin", _K.FLOAT, 0.10,
               "Required lead of the best match over the runner-up.", "metadata",
               min_value=0.0, max_value=1.0),
    SettingDef("metadata.certification_country", _K.STR, "SA",
               "Country whose age certifications are shown (ISO 3166-1 alpha-2).", "metadata",
               country_code),
    SettingDef("metadata.genre_category_map", _K.STR,
               json.dumps(dict(DEFAULT_GENRE_CATEGORY_MAP), separators=(",", ":")),
               "TMDB genre ids mapped to category slugs, as a JSON object.", "metadata",
               genre_category_map),
    # Billing (SPEC §7.6)
    SettingDef("billing.vat_rate", _K.FLOAT, 0.15, "VAT rate applied to invoices.", "billing",
               min_value=0.0, max_value=1.0),
    SettingDef("billing.currency", _K.STR, "SAR", "Default currency (ISO 4217).", "billing",
               currency_code),
    SettingDef("billing.grace_days", _K.INT, 3,
               "Days a subscription stays in grace after it ends.", "billing",
               min_value=0, max_value=60),
    # Trials
    SettingDef("trials.duration_hours", _K.INT, 24, "Length of a free trial, in hours.",
               "trials", min_value=1, max_value=720),
    SettingDef("trials.limit_per_phone", _K.INT, 1, "Free trials allowed per phone number.",
               "trials", min_value=0, max_value=10),
    # Security (SPEC §8.2, §11)
    SettingDef("security.admin_idle_timeout_min", _K.INT, 30,
               "Admin sessions end after this many idle minutes.", "security",
               min_value=5, max_value=480),
    # Feature flags
    SettingDef("features.subtitle_download", _K.BOOL, False,
               "Download subtitles from external providers.", "features"),
    SettingDef("features.machine_translation", _K.BOOL, False,
               "Machine-translate missing Arabic metadata.", "features"),
    SettingDef("features.include_vod_in_m3u", _K.BOOL, True,
               "Include movies and series in the M3U playlist.", "features"),
    SettingDef("features.approve_new_devices", _K.BOOL, False,
               "New devices need approval before they can play.", "features"),
)  # fmt: skip


def build_registry(definitions: Iterable[SettingDef]) -> Mapping[str, SettingDef]:
    """Index definitions by key, refusing duplicates, unknown groups and bad defaults."""
    registry: dict[str, SettingDef] = {}
    for definition in definitions:
        if definition.key in registry:
            msg = f"Setting {definition.key} is declared twice"
            raise ImproperlyConfigured(msg)
        if definition.group not in GROUPS or not definition.key.startswith(f"{definition.group}."):
            msg = f"Setting {definition.key} has an unknown or mismatched group {definition.group}"
            raise ImproperlyConfigured(msg)
        try:
            definition.clean(definition.default)
        except ValidationError as exc:
            msg = f"Setting {definition.key} has an invalid default: {exc.messages}"
            raise ImproperlyConfigured(msg) from exc
        registry[definition.key] = definition
    return MappingProxyType(registry)


REGISTRY: Mapping[str, SettingDef] = build_registry(_DEFINITIONS)


def get_definition(key: str) -> SettingDef:
    try:
        return REGISTRY[key]
    except KeyError:
        raise UnknownSettingError(key) from None


def definitions() -> list[SettingDef]:
    """All definitions in declaration order (grouped as the admin UI shows them)."""
    return list(REGISTRY.values())
