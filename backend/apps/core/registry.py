"""The typed settings registry (SPEC §6 ops, §8.3 Settings).

Every runtime-tunable value (branding, token TTLs, thresholds, feature flags,
integration credentials) is declared here once with its type, default, constraints
and group. The database (`core.Setting`) stores admin overrides only. Read values
with `apps.core.services.get_setting` (secrets: `get_secret`), change them with
`set_setting`.

Secrets (`sensitive=True`) are encrypted at rest, never returned by the API and
audited without values. A setting may name server-config variables (`env`) as its
fallback: the effective value is the admin's stored value, else the variable (set in
`.env` before the admin existed), else the default (ADR-0019).
"""

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from email.utils import parseaddr
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import EmailValidator, URLValidator

from apps.accounts.validators import validate_e164_phone, validate_timezone

type SettingValue = bool | int | float | str
#: Turns the raw server-config values of a setting's `env` names (None: unset) into
#: one value for the setting, or None when they don't provide one.
type EnvResolver = Callable[[Mapping[str, object | None]], object | None]


class SettingKind(StrEnum):
    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    STR = "str"


class UnknownSettingError(KeyError):
    """The key is not declared in the registry."""


#: Widgets the admin renders instead of a text input (the value stays a string).
#: "image": an upload; "text": long text (Markdown) with a preview (ADR-0023).
WIDGETS: tuple[str, ...] = ("image", "text")

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def parse_text(kind: SettingKind, raw: str) -> object:
    """Text (stdin, an environment variable) into the setting's type; the registry
    checks the rest. Raises ValidationError."""
    text = raw.strip() if kind != SettingKind.STR else raw.rstrip("\r\n")
    if kind == SettingKind.BOOL:
        if text.lower() in _TRUE:
            return True
        if text.lower() in _FALSE:
            return False
        msg = "Expected true or false."
        raise ValidationError(msg, code="invalid_type")
    try:
        if kind == SettingKind.INT:
            return int(text)
        if kind == SettingKind.FLOAT:
            return float(text)
    except ValueError:
        msg = f"Expected a value of type {kind.value}."
        raise ValidationError(msg, code="invalid_type") from None
    return text


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
    #: Server-config variables (Django settings, else the environment) used when the
    #: admin has stored nothing; the first one set wins unless `env_resolve` decides.
    env: tuple[str, ...] = field(default=(), kw_only=True)
    env_resolve: EnvResolver | None = field(default=None, kw_only=True)
    #: How the admin edits it when a text input won't do ("image": an upload).
    widget: str | None = field(default=None, kw_only=True)

    def clean(self, value: object, *, check_format: bool = True) -> SettingValue:
        """Return `value` converted to this setting's type, or raise ValidationError.

        `check_format=False` skips the format validator (type, range and choices still
        apply): a server-config value set before the validator existed keeps working.
        """
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
        if check_format and self.validator is not None:
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

    def from_server_config(self, raw: Mapping[str, object | None]) -> SettingValue | None:
        """This setting's value from its server-config variables, or None when they hold
        none. Raises ValidationError for a value of the wrong type or out of range; the
        format validator is not applied (`.env` values from before it keep working)."""
        if self.env_resolve is not None:
            candidate = self.env_resolve(raw)
        else:
            candidate = next((raw[name] for name in self.env if raw.get(name) is not None), None)
        if candidate is None:
            return None
        if isinstance(candidate, str) and self.kind != SettingKind.STR:
            candidate = parse_text(self.kind, candidate)
        elif self.kind == SettingKind.STR and not isinstance(candidate, str):
            candidate = str(candidate)
        return self.clean(candidate, check_format=False)


# --- Validators -------------------------------------------------------------------

_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_COUNTRY = re.compile(r"[A-Z]{2}")


def _pattern(regex: re.Pattern[str], message: str, code: str = "invalid") -> Callable[[Any], None]:
    """A format check. `code` is the stable code the admin translates (ADR-0015, ADR-0023)."""

    def validate(value: Any) -> None:
        if not regex.fullmatch(value):
            raise ValidationError(message, code=code)

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
        raise ValidationError(message, code="invalid_genre_map") from None
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and key.isdigit() and isinstance(slug, str) and _SLUG.fullmatch(slug)
        for key, slug in data.items()
    ):
        raise ValidationError(message, code="invalid_genre_map")


hex_color = _pattern(_HEX_COLOR, "Enter a colour as #RRGGBB.", "invalid_color")
currency_code = _pattern(
    _CURRENCY, "Enter an ISO 4217 currency code, e.g. SAR.", "invalid_currency"
)
country_code = _pattern(
    _COUNTRY, "Enter an ISO 3166-1 alpha-2 country code, e.g. SA.", "invalid_country"
)
optional_email = _optional(EmailValidator(code="invalid_email"))
optional_http_url = _optional(URLValidator(schemes=["http", "https"], code="invalid_url"))
# --- Billing validators (B1, ADR-0012) ---
invoice_prefix = _pattern(
    re.compile(r"[A-Z0-9]{1,10}"),
    "Use 1 to 10 capital letters or digits.",
    "invalid_invoice_prefix",
)
vat_number = _optional(
    _pattern(re.compile(r"[0-9A-Z]{5,20}"), "Use 5 to 20 letters or digits.", "invalid_vat_number")
)
# --- end B1 ---
# --- Monitoring validators (O1, ADR-0018) ---
ALERT_SEVERITY_CHOICES: tuple[str, ...] = ("all", "critical", "none")
_TELEGRAM_TOKEN = re.compile(r"[0-9]{5,20}:[A-Za-z0-9_-]{30,100}")
telegram_bot_token = _optional(
    _pattern(
        _TELEGRAM_TOKEN,
        "Enter the bot token @BotFather gave, e.g. 123456789:AA...",
        "invalid_telegram_token",
    )
)
telegram_chat_id = _optional(
    _pattern(
        re.compile(r"-?[0-9]{1,20}"),
        "Enter the chat id, a number such as -1001234567890.",
        "invalid_telegram_chat",
    )
)


def alert_email_list(value: Any) -> None:
    """Email addresses separated by commas (spaces allowed); empty: none."""
    for address in (part.strip() for part in str(value).split(",")):
        if address:
            EmailValidator(message=f"{address} is not an email address.", code="invalid_email")(
                address
            )


# --- end O1 ---
# --- Integrations, email and branding (CFG, ADR-0019) ---
SMTP_SECURITY_CHOICES: tuple[str, ...] = ("starttls", "ssl", "none")
LANGUAGE_CHOICES: tuple[str, ...] = ("ar", "en")
host_name = _optional(
    _pattern(
        re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?"),
        "Enter a host name or address, e.g. smtp.example.com.",
        "invalid_host",
    )
)
tmdb_read_access_token = _optional(
    _pattern(
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        "Paste TMDB's API Read Access Token (a long text starting with eyJ).",
        "invalid_tmdb_token",
    )
)
tmdb_api_key = _optional(
    _pattern(
        re.compile(r"[0-9a-f]{32}"), "A TMDB API key is 32 letters and digits.", "invalid_tmdb_key"
    )
)
tmdb_language = _pattern(
    re.compile(r"[a-z]{2}-[A-Z]{2}"), "Use a language tag such as en-US.", "invalid_language_tag"
)
tvdb_api_key = _optional(
    _pattern(
        re.compile(r"[A-Za-z0-9-]{16,64}"),
        "Paste the API key TheTVDB gave your project.",
        "invalid_tvdb_key",
    )
)
tvdb_pin = _optional(
    _pattern(
        re.compile(r"[A-Za-z0-9]{1,32}"), "Use the PIN's letters and digits.", "invalid_tvdb_pin"
    )
)
stripe_secret_key = _optional(
    _pattern(
        re.compile(r"(?:sk|rk)_(?:test|live)_[A-Za-z0-9]{10,250}"),
        "A Stripe secret key starts with sk_test_, sk_live_, rk_test_ or rk_live_.",
        "invalid_stripe_key",
    )
)
stripe_webhook_secret = _optional(
    _pattern(
        re.compile(r"whsec_[A-Za-z0-9+/=]{10,200}"),
        "A Stripe webhook signing secret starts with whsec_.",
        "invalid_stripe_webhook_secret",
    )
)
moyasar_secret_key = _optional(
    _pattern(
        re.compile(r"sk_(?:test|live)_[A-Za-z0-9]{10,200}"),
        "A Moyasar secret key starts with sk_test_ or sk_live_.",
        "invalid_moyasar_key",
    )
)
webhook_token = _optional(
    _pattern(
        re.compile(r"[!-~]{8,200}"),
        "Use 8 to 200 characters without spaces.",
        "invalid_webhook_token",
    )
)


def _phone(value: Any) -> None:
    try:
        validate_e164_phone(value)
    except ValidationError:
        msg = "Enter the number with its country code, e.g. +966501234567."
        raise ValidationError(msg, code="invalid_phone") from None


optional_phone = _optional(_phone)
#: Logos are uploaded (the admin's image widget), stored under the media volume.
branding_image = _optional(
    _pattern(
        re.compile(r"images/branding/logo-(?:light|dark)-[0-9a-f]{12}\.png"),
        "Upload the logo; its stored name is set for you.",
        "invalid_logo",
    )
)


def email_sender(value: Any) -> None:
    """An address, or "Name <address>"; empty means the default sender."""
    text = str(value)
    if text == "":
        return
    message = 'Enter an address, or a name and an address: "Smart IPTV <no-reply@example.com>".'
    if "\n" in text or "\r" in text:
        raise ValidationError(message, code="invalid_sender")
    _name, address = parseaddr(text)
    if not address:
        raise ValidationError(message, code="invalid_sender")
    EmailValidator(message=message, code="invalid_sender")(address)


def smtp_security_from_env(raw: Mapping[str, object | None]) -> str | None:
    """`EMAIL_USE_SSL`/`EMAIL_USE_TLS` (Django's pair) as one choice."""

    def flag(name: str) -> bool | None:
        value = raw.get(name)
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        return True if text in _TRUE else False if text in _FALSE else None

    ssl, tls = flag("EMAIL_USE_SSL"), flag("EMAIL_USE_TLS")
    if ssl:
        return "ssl"
    if tls is None:
        return None
    return "starttls" if tls else "none"


def seconds_to_hours_from_env(raw: Mapping[str, object | None]) -> int | None:
    """`TRANSCODE_JOB_TIMEOUT_S` (seconds) as whole hours, rounded up."""
    value = next((v for v in raw.values() if v is not None), None)
    if value is None:
        return None
    try:
        seconds = int(str(value).strip())
    except ValueError:
        return None
    return max(1, -(-seconds // 3600))


# --- end CFG ---


# --- The registry -------------------------------------------------------------------

# --- Backups, privacy and CrowdSec validators (H2, ADR-0020) ---
_CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
clock_time = _pattern(_CLOCK, "Enter a time as HH:MM (24-hour), e.g. 03:30.", "invalid_time")
_S3_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
s3_bucket = _optional(
    _pattern(_S3_BUCKET, "Enter a bucket name, e.g. my-backups.", "invalid_bucket")
)
s3_prefix = _pattern(
    re.compile(r"[A-Za-z0-9._/-]{0,200}"),
    "Use letters, digits, dots, dashes and slashes.",
    "invalid_prefix",
)
s3_region = _optional(
    _pattern(re.compile(r"[a-z0-9-]{1,40}"), "Enter a region, e.g. eu-central-1.", "invalid_region")
)
_S3_CREDENTIAL = re.compile(r"[A-Za-z0-9/+=._-]{3,256}")
s3_credential = _optional(
    _pattern(_S3_CREDENTIAL, "Paste the key exactly as the provider gave it.", "invalid_key")
)


def time_zone(value: Any) -> None:
    """An IANA time zone name such as Asia/Riyadh or UTC."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # noqa: PLC0415

    try:
        ZoneInfo(str(value))
    except (ZoneInfoNotFoundError, ValueError):
        msg = "Enter a time zone such as Asia/Riyadh or UTC."
        raise ValidationError(msg, code="invalid_timezone") from None


def network_list(value: Any) -> None:
    """IP addresses and CIDR networks separated by commas; empty: none."""
    import ipaddress  # noqa: PLC0415

    for part in (item.strip() for item in str(value).split(",")):
        if not part:
            continue
        try:
            ipaddress.ip_network(part, strict=False)
        except ValueError:
            msg = f"{part} is not an IP address or network (e.g. 203.0.113.0/24)."
            raise ValidationError(msg, code="invalid_network") from None


# --- end H2 ---


# --- Application security (M14a, ADR-0021) ---
_CSP_ORIGIN = re.compile(
    r"https?://(?:\*\.)?[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::[0-9]{1,5})?"
)


def origin_list(value: Any) -> None:
    """Web origins separated by commas (https://cdn.example.com, https://*.example.net);
    empty: none. Strict, because the list goes into the portal's and the admin's
    Content-Security-Policy header."""
    for part in (item.strip() for item in str(value).split(",")):
        if part and not _CSP_ORIGIN.fullmatch(part):
            msg = f"{part} is not an origin such as https://cdn.example.com."
            raise ValidationError(msg, code="invalid_origin")


# --- end M14a ---


# --- Scale and CDN (M15, ADR-0022) ---
_S3_ENDPOINT = re.compile(
    r"https?://[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::[0-9]{1,5})?/?"
)
s3_endpoint = _optional(
    _pattern(
        _S3_ENDPOINT, "Enter the service's address, e.g. https://s3.example.com.", "invalid_url"
    )
)
# --- end M15 ---


# --- Legal pages (Q1, ADR-0023) ---
_LEGAL_TEMPLATES = Path(__file__).with_name("legal_templates")
#: The longest legal text an admin may store (characters).
LEGAL_MAX_CHARS = 60_000


def legal_template(document: str, language: str) -> str:
    """The starting text of a legal page (Markdown; to be reviewed by a lawyer)."""
    return (_LEGAL_TEMPLATES / f"{document}.{language}.md").read_text(encoding="utf-8")


def legal_text(value: Any) -> None:
    if len(value) > LEGAL_MAX_CHARS:
        msg = f"Keep it under {LEGAL_MAX_CHARS} characters."
        raise ValidationError(msg, code="too_long")


_INGEST_USER = re.compile(r"[A-Za-z0-9._-]{1,64}")
ingest_user = _pattern(_INGEST_USER, "Use 1 to 64 letters, digits, dots, dashes or underscores.",
                       "invalid_ingest_user")  # fmt: skip


def ingest_password(value: Any) -> None:
    """Encoders put it in URLs (rtmp://user:password@host): printable, no spaces."""
    if value and not (8 <= len(value) <= 200 and value.isprintable() and " " not in value):
        msg = "Use 8 to 200 printable characters without spaces."
        raise ValidationError(msg, code="invalid_ingest_password")


# --- end Q1 ---


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
    "live",
    "monitoring",
    "email",
    "notifications",
    "backups",
    "privacy",
    "geoip",
    "antisharing",
    # M15 (ADR-0022)
    "edges",
    "storage",
    "cdn",
    "database",
    # Q1 (ADR-0023)
    "legal",
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
    # --- Branding and business (CFG, ADR-0019) ---
    SettingDef("branding.support_phone", _K.STR, "",
               "Support phone number, with its country code (+966...).", "branding",
               optional_phone),
    SettingDef("branding.support_whatsapp", _K.STR, "",
               "Support WhatsApp number, with its country code (+966...).", "branding",
               optional_phone),
    SettingDef("branding.logo_light", _K.STR, "",
               "Logo on light backgrounds (PNG, JPEG or WebP; shown in the apps, emails "
               "and invoices).", "branding", branding_image, widget="image"),
    SettingDef("branding.logo_dark", _K.STR, "",
               "Logo on dark backgrounds (the dark theme); the light logo when empty.",
               "branding", branding_image, widget="image"),
    SettingDef("branding.default_language", _K.STR, "ar",
               "Language of new customers and of first visits to the portal.", "branding",
               choices=LANGUAGE_CHOICES),
    SettingDef("branding.time_zone", _K.STR, "Asia/Riyadh",
               "Time zone of new customers, the dashboard's days and invoice dates (IANA).",
               "branding", validate_timezone),
    # --- end CFG ---
    # Xtream server_info (SPEC §7.5)
    SettingDef("xtream.server_url", _K.STR, "",
               "Public base URL IPTV apps use as the server, e.g. https://tv.example.com.",
               "xtream", optional_http_url),
    SettingDef("xtream.port", _K.INT, 80, "HTTP port reported to IPTV apps.", "xtream",
               min_value=1, max_value=65535),
    SettingDef("xtream.https_port", _K.INT, 443, "HTTPS port reported to IPTV apps.", "xtream",
               min_value=1, max_value=65535),
    SettingDef("xtream.password_min_length", _K.INT, 8,
               "Shortest password an admin may choose for an IPTV app login.", "xtream",
               min_value=6, max_value=64),
    SettingDef("xtream.auth_failures_per_username", _K.INT, 30,
               "Failed IPTV app sign-ins per username before sign-ins pause for the window.",
               "xtream", min_value=3, max_value=1000),
    SettingDef("xtream.auth_failures_per_ip", _K.INT, 60,
               "Failed IPTV app sign-ins per client IP before sign-ins pause for the window.",
               "xtream", min_value=5, max_value=10000),
    SettingDef("xtream.auth_failure_window_s", _K.INT, 900,
               "Window, in seconds, over which failed IPTV app sign-ins are counted.",
               "xtream", min_value=60, max_value=86400),
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
    # --- Web playback progress (C1, ADR-0013) ---
    SettingDef("playback.progress_min_interval_s", _K.INT, 5,
               "Progress reports closer together than this, in seconds, are not saved.",
               "playback", min_value=0, max_value=300),
    SettingDef("playback.watched_ratio", _K.FLOAT, 0.9,
               "Share of a title's runtime after which it counts as watched.", "playback",
               min_value=0.5, max_value=1.0),
    # --- end C1 ---
    # Library scanning (SPEC §7.1)
    SettingDef("library.watcher_stable_s", _K.INT, 60,
               "A new file is scanned once its size has not changed for this many seconds.",
               "library", min_value=5, max_value=3600),
    # --- CFG (ADR-0019): formerly environment variables ---
    SettingDef("library.watcher_polling", _K.BOOL, False,
               "Poll the libraries for changes instead of listening for them (network "
               "shares such as NFS or SMB, where change events never arrive).", "library",
               env=("LIBRARY_WATCHER_POLLING",)),
    SettingDef("library.transcode_max_attempts", _K.INT, 3,
               "Attempts per transcode job before it fails for good.", "library",
               min_value=1, max_value=10, env=("TRANSCODE_MAX_ATTEMPTS",)),
    SettingDef("library.transcode_retry_backoff_s", _K.INT, 60,
               "Seconds before a failed transcode job is retried (doubled each time).",
               "library", min_value=0, max_value=3600, env=("TRANSCODE_RETRY_BACKOFF_S",)),
    SettingDef("library.transcode_job_timeout_h", _K.INT, 12,
               "A transcode running longer than this many hours is stopped.", "library",
               min_value=1, max_value=72, env=("TRANSCODE_JOB_TIMEOUT_S",),
               env_resolve=seconds_to_hours_from_env),
    # --- end CFG ---
    # Media processing (SPEC §7.3, ADR-0014; T1)
    SettingDef("library.hls_enabled", _K.BOOL, True,
               "Encode the HLS ladder (adaptive streaming for the portal) at ingest.",
               "library"),
    SettingDef("library.uhd_enabled", _K.BOOL, True,
               "Make a UHD version of 4K sources for plans with 2160p quality.", "library"),
    SettingDef("library.uhd_cpu_encode", _K.BOOL, False,
               "Allow UHD HEVC encodes on CPU-only transcoders (very slow; keeping a "
               "suitable source needs no encode).", "library"),
    SettingDef("library.capped_compat_mp4", _K.STR, "auto",
               "Remux a 720p or 480p compat MP4 from the ladder for plans capped there, so "
               "IPTV apps on those plans get an MP4 (auto: the ceilings plans use; off).",
               "library", choices=("auto", "off")),
    SettingDef("library.rendition_retention_days", _K.INT, 7,
               "Days the renditions of a removed file are kept before cleanup deletes them.",
               "library", min_value=0, max_value=365),
    # --- I1 (ADR-0025): imports from storage and direct links ---
    SettingDef("library.import_max_concurrent", _K.INT, 1,
               "Imports that copy at the same time; the others wait in the queue.", "library",
               min_value=1, max_value=2),
    SettingDef("library.import_reserve_gb", _K.INT, 10,
               "Free space (GB) an import must leave on the library's disk; an import that "
               "wouldn't is refused before it copies anything.", "library",
               min_value=0, max_value=100_000),
    SettingDef("library.import_bandwidth_mbit", _K.INT, 0,
               "Bandwidth limit for imports in Mbit/s (0: no limit).", "library",
               min_value=0, max_value=100_000),
    # --- end I1 ---
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
    # --- TMDB and TheTVDB credentials (CFG, ADR-0019) ---
    SettingDef("metadata.tmdb_read_access_token", _K.STR, "",
               "TMDB API Read Access Token (preferred); without a credential, metadata comes "
               "from the offline sample fixtures.", "metadata", tmdb_read_access_token,
               sensitive=True, env=("TMDB_READ_ACCESS_TOKEN",)),
    SettingDef("metadata.tmdb_api_key", _K.STR, "",
               "TMDB API key (v3), used when no Read Access Token is set.", "metadata",
               tmdb_api_key, sensitive=True, env=("TMDB_API_KEY",)),
    SettingDef("metadata.tmdb_language", _K.STR, "en-US",
               "Language of TMDB's main metadata; Arabic is always fetched as well.",
               "metadata", tmdb_language, env=("TMDB_LANGUAGE",)),
    SettingDef("metadata.tmdb_rate_limit_per_s", _K.INT, 35,
               "Most TMDB requests per second, shared by every worker.", "metadata",
               min_value=1, max_value=50, env=("TMDB_RATE_LIMIT_PER_S",)),
    SettingDef("metadata.tmdb_cache_ttl_s", _K.INT, 86400,
               "Seconds TMDB responses are cached.", "metadata",
               min_value=3600, max_value=604800, env=("TMDB_CACHE_TTL_S",)),
    SettingDef("metadata.tvdb_api_key", _K.STR, "",
               "TheTVDB API key, for series TMDB can't match confidently.", "metadata",
               tvdb_api_key, sensitive=True, env=("THETVDB_API_KEY",)),
    SettingDef("metadata.tvdb_pin", _K.STR, "",
               "TheTVDB subscriber PIN, needed only with a user-supported key.", "metadata",
               tvdb_pin, sensitive=True),
    # --- end CFG ---
    # TheTVDB fallback (Q1, ADR-0023)
    SettingDef("metadata.tvdb_rate_limit_per_s", _K.INT, 10,
               "TheTVDB requests per second, shared by every worker.", "metadata",
               min_value=1, max_value=50),
    # Billing (SPEC §7.6)
    SettingDef("billing.vat_rate", _K.FLOAT, 0.15, "VAT rate applied to invoices.", "billing",
               min_value=0.0, max_value=1.0),
    SettingDef("billing.currency", _K.STR, "SAR", "Default currency (ISO 4217).", "billing",
               currency_code),
    SettingDef("billing.grace_days", _K.INT, 3,
               "Days a subscription stays in grace after it ends.", "billing",
               min_value=0, max_value=60),
    # --- Billing: prices, invoices, checkout and providers (B1, ADR-0012) ---
    SettingDef("billing.prices_include_vat", _K.BOOL, True,
               "Plan prices include VAT (VAT is carved out of them on invoices).", "billing"),
    SettingDef("billing.invoice_prefix", _K.STR, "INV",
               "Prefix of invoice numbers (PREFIX-YEAR-SEQUENCE).", "billing",
               invoice_prefix),
    SettingDef("billing.vat_number", _K.STR, "", "The seller's VAT registration number.",
               "billing", vat_number),
    SettingDef("billing.seller_address", _K.STR, "", "The seller's address on invoices.",
               "billing"),
    SettingDef("billing.manual_instructions_en", _K.STR,
               "Transfer the amount to our bank account and send us the receipt; "
               "quote the reference below.",
               "How to pay by bank transfer or cash (English).", "billing"),
    SettingDef("billing.manual_instructions_ar", _K.STR,
               "حوّل المبلغ إلى حسابنا البنكي وأرسل لنا الإيصال مع ذكر الرقم المرجعي أدناه.",
               "How to pay by bank transfer or cash (Arabic).", "billing"),
    SettingDef("billing.stripe_enabled", _K.BOOL, False,
               "Offer card payments through Stripe (needs its secret key and webhook secret).",
               "billing"),
    SettingDef("billing.moyasar_enabled", _K.BOOL, False,
               "Offer mada, Apple Pay and STC Pay through Moyasar (needs its secret key and "
               "webhook secret).", "billing"),
    # --- Payment provider credentials (CFG, ADR-0019) ---
    SettingDef("billing.stripe_secret_key", _K.STR, "",
               "Stripe secret key (sk_test_... uses Stripe's sandbox).", "billing",
               stripe_secret_key, sensitive=True, env=("STRIPE_SECRET_KEY",)),
    SettingDef("billing.stripe_webhook_secret", _K.STR, "",
               "Signing secret of the Stripe webhook endpoint (whsec_...).", "billing",
               stripe_webhook_secret, sensitive=True, env=("STRIPE_WEBHOOK_SECRET",)),
    SettingDef("billing.moyasar_secret_key", _K.STR, "",
               "Moyasar secret key (sk_test_... uses Moyasar's sandbox).", "billing",
               moyasar_secret_key, sensitive=True, env=("MOYASAR_SECRET_KEY",)),
    SettingDef("billing.moyasar_webhook_secret", _K.STR, "",
               "The secret token Moyasar sends with each webhook.", "billing",
               webhook_token, sensitive=True, env=("MOYASAR_WEBHOOK_SECRET",)),
    # --- end CFG ---
    SettingDef("billing.checkout_ttl_hours", _K.INT, 24,
               "Unpaid checkouts are voided after this many hours.", "billing",
               min_value=1, max_value=168),
    # --- end B1 ---
    # Trials
    SettingDef("trials.duration_hours", _K.INT, 24, "Length of a free trial, in hours.",
               "trials", min_value=1, max_value=720),
    SettingDef("trials.limit_per_phone", _K.INT, 1, "Free trials allowed per phone number.",
               "trials", min_value=0, max_value=10),
    # --- Trials (B1, ADR-0012) ---
    SettingDef("trials.require_approval", _K.BOOL, True,
               "A customer's free trial waits for an admin's approval.", "trials"),
    # --- end B1 ---
    # Security (SPEC §8.2, §11)
    SettingDef("security.admin_idle_timeout_min", _K.INT, 30,
               "Admin sessions end after this many idle minutes.", "security",
               min_value=5, max_value=480),
    # --- Customer API sign-in (C1, ADR-0013) ---
    SettingDef("security.customer_session_days", _K.INT, 30,
               "Portal sign-ins end after this many idle days.", "security",
               min_value=1, max_value=365),
    # Q1 (ADR-0023): browsers reuse their device; unused ones leave the lists.
    SettingDef("security.web_device_idle_days", _K.INT, 30,
               "A browser unused this many days leaves the customer's devices.", "security",
               min_value=1, max_value=365),
    SettingDef("security.customer_access_token_ttl_s", _K.INT, 600,
               "Lifetime of app access tokens, in seconds.", "security",
               min_value=60, max_value=3600),
    SettingDef("security.customer_refresh_token_days", _K.INT, 30,
               "App sign-ins end after this many days without a token refresh.", "security",
               min_value=1, max_value=365),
    SettingDef("security.customer_auth_requests_per_ip", _K.INT, 60,
               "Failed customer sign-ins and password-reset requests per client IP in 15 "
               "minutes before they are refused.", "security", min_value=5, max_value=10000),
    SettingDef("security.password_reset_ttl_min", _K.INT, 60,
               "A password-reset link works for this many minutes.", "security",
               min_value=5, max_value=1440),
    SettingDef("security.password_invite_ttl_days", _K.INT, 7,
               "A set-your-password invitation works for this many days.", "security",
               min_value=1, max_value=30),
    SettingDef("security.password_reset_emails_per_hour", _K.INT, 3,
               "Password-reset emails one customer can receive per hour.", "security",
               min_value=1, max_value=20),
    # --- end C1 ---
    # Feature flags
    SettingDef("features.subtitle_download", _K.BOOL, False,
               "Download subtitles from external providers.", "features"),
    SettingDef("features.machine_translation", _K.BOOL, False,
               "Machine-translate missing Arabic metadata.", "features"),
    SettingDef("features.include_vod_in_m3u", _K.BOOL, True,
               "Include movies and series in the M3U playlist.", "features"),
    SettingDef("features.approve_new_devices", _K.BOOL, False,
               "New devices need approval before they can play.", "features"),
    # --- Live TV and EPG (M12, ADR-0017) ---
    SettingDef("live.idle_stop_s", _K.INT, 150,
               "Seconds a channel keeps packaging after its last viewer (on-demand channels).",
               "live", min_value=60, max_value=3600),
    SettingDef("live.max_running_channels", _K.INT, 20,
               "Most channels packaged at once; more viewers of other channels wait.", "live",
               min_value=1, max_value=500),
    SettingDef("live.realtime_transcode_max", _K.INT, 1,
               "Most channels re-encoded in real time at once (the capped fallback).", "live",
               min_value=0, max_value=16),
    SettingDef("live.catchup_max_days", _K.INT, 7,
               "Longest catch-up window a channel may keep, in days.", "live",
               min_value=1, max_value=30),
    SettingDef("live.timeshift_max_minutes", _K.INT, 480,
               "Longest catch-up window one request may play, in minutes.", "live",
               min_value=10, max_value=1440),
    SettingDef("live.epg_past_days", _K.INT, 7,
               "Days of past programmes the guide keeps.", "live", min_value=1, max_value=30),
    SettingDef("live.epg_future_days", _K.INT, 14,
               "Days of future programmes an import keeps.", "live", min_value=1, max_value=60),
    SettingDef("live.archive_max_gb", _K.INT, 20,
               "Disk budget of the catch-up archive across channels, in GB; the oldest "
               "recorded hours go first beyond it.", "live", min_value=1, max_value=100000),
    SettingDef("live.epg_max_document_mb", _K.INT, 200,
               "Largest XMLTV document an import reads, in megabytes (uncompressed).", "live",
               min_value=1, max_value=2000),
    # Live ingest sign-ins (Q1, ADR-0023): MediaMTX asks /internal/mediamtx-auth. The
    # .env users of before (MEDIAMTX_*) keep working until moved into the settings.
    SettingDef("live.ingest_publish_user", _K.STR, "publisher",
               "The user encoders, cameras and OBS publish with.", "live", ingest_user,
               env=("MEDIAMTX_PUBLISH_USER",)),
    SettingDef("live.ingest_publish_password", _K.STR, "",
               "The password encoders, cameras and OBS publish with.", "live", ingest_password,
               sensitive=True, env=("MEDIAMTX_PUBLISH_PASS",)),
    SettingDef("live.ingest_read_user", _K.STR, "reader",
               "The user the live packager reads ingested streams with.", "live", ingest_user,
               env=("MEDIAMTX_READ_USER",)),
    SettingDef("live.ingest_read_password", _K.STR, "",
               "The password the live packager reads ingested streams with.", "live",
               ingest_password, sensitive=True, env=("MEDIAMTX_READ_PASS",)),
    SettingDef("live.ingest_auth_max_failures", _K.INT, 20,
               "Failed ingest sign-ins from one address before it is refused for the window.",
               "live", min_value=3, max_value=1000),
    SettingDef("live.ingest_auth_block_minutes", _K.INT, 15,
               "How long an address with too many failed ingest sign-ins is refused.", "live",
               min_value=1, max_value=1440),
    # --- end M12 ---
    # --- Monitoring and alerts (O1, ADR-0018) ---
    SettingDef("monitoring.alerts_enabled", _K.BOOL, True,
               "Send the monitoring alerts by email and Telegram.", "monitoring"),
    SettingDef("monitoring.alert_emails", _K.STR, "",
               "Who receives alert emails, separated by commas; empty: no alert email.",
               "monitoring", alert_email_list, env=("ALERT_EMAIL_TO",)),
    SettingDef("monitoring.alert_email_severities", _K.STR, "all",
               "Which alerts are emailed: all, critical only, or none.", "monitoring",
               choices=ALERT_SEVERITY_CHOICES),
    SettingDef("monitoring.telegram_severities", _K.STR, "all",
               "Which alerts go to Telegram: all, critical only, or none.", "monitoring",
               choices=ALERT_SEVERITY_CHOICES),
    SettingDef("monitoring.edge_capacity_mbps", _K.INT, 1000,
               "The media edge's uplink in Mbit/s; an alert fires above 80% of it.",
               "monitoring", min_value=10, max_value=1_000_000),
    SettingDef("monitoring.session_hours", _K.INT, 8,
               "A monitoring sign-in (Grafana, Prometheus, Alertmanager) lasts this many hours.",
               "monitoring", min_value=1, max_value=24),
    # --- end O1 ---
    SettingDef("monitoring.runbook_base_url", _K.STR,
               "https://github.com/mah007/iptv/blob/main/",
               "Where alert messages link the runbooks (docs/runbooks/alerts.md) to.",
               "monitoring", optional_http_url, env=("RUNBOOK_BASE_URL",)),
    # --- Email (SPEC §7.7; CFG, ADR-0019) ---
    SettingDef("email.smtp_host", _K.STR, "",
               "SMTP server for every email; empty: emails wait in the outbox.", "email",
               host_name, env=("EMAIL_HOST",)),
    SettingDef("email.smtp_port", _K.INT, 587, "SMTP port (587 STARTTLS, 465 SSL, 25 none).",
               "email", min_value=1, max_value=65535, env=("EMAIL_PORT",)),
    SettingDef("email.smtp_security", _K.STR, "starttls",
               "Encryption: STARTTLS, SSL/TLS from the start, or none.", "email",
               choices=SMTP_SECURITY_CHOICES, env=("EMAIL_USE_SSL", "EMAIL_USE_TLS"),
               env_resolve=smtp_security_from_env),
    SettingDef("email.smtp_username", _K.STR, "", "SMTP user name, if the server needs one.",
               "email", env=("EMAIL_HOST_USER",)),
    SettingDef("email.smtp_password", _K.STR, "", "SMTP password.", "email",
               sensitive=True, env=("EMAIL_HOST_PASSWORD",)),
    SettingDef("email.from_address", _K.STR, "",
               "Sender of every email, e.g. \"Smart IPTV <no-reply@example.com>\"; empty: the "
               "service name at no-reply@ the domain.", "email", email_sender,
               env=("DEFAULT_FROM_EMAIL",)),
    SettingDef("email.timeout_s", _K.INT, 20,
               "Seconds to wait for the SMTP server before an attempt fails.", "email",
               min_value=5, max_value=120, env=("EMAIL_TIMEOUT",)),
    # --- Notification channels (SPEC §7.7; CFG, ADR-0019) ---
    SettingDef("notifications.telegram_bot_token", _K.STR, "",
               "The Telegram bot that sends alerts and messages (from @BotFather).",
               "notifications", telegram_bot_token, sensitive=True,
               env=("ALERT_TELEGRAM_BOT_TOKEN",)),
    SettingDef("notifications.telegram_chat_id", _K.STR, "",
               "The admins' Telegram chat (or group, or channel): alerts go there.",
               "notifications", telegram_chat_id, env=("ALERT_TELEGRAM_CHAT_ID",)),
    # --- end CFG ---
    # --- Backups (H2, ADR-0020): the backup service asks Django for these ---
    SettingDef("backups.enabled", _K.BOOL, True,
               "Back up the database (with point-in-time recovery), the configuration and "
               "the artwork.", "backups"),
    SettingDef("backups.schedule_time", _K.STR, "03:30",
               "Time of the nightly backup (HH:MM, in the time zone below).", "backups",
               clock_time),
    SettingDef("backups.schedule_timezone", _K.STR, "UTC",
               "Time zone of the backup and restore drill times, e.g. Asia/Riyadh.", "backups",
               time_zone),
    SettingDef("backups.full_weekday", _K.INT, 6,
               "Day of the full database backup (0 Monday ... 6 Sunday); other nights take a "
               "differential backup.", "backups", min_value=0, max_value=6),
    SettingDef("backups.db_full_retention", _K.INT, 2,
               "Full database backups kept on this server, each with its differential "
               "backups and WAL.", "backups", min_value=1, max_value=52),
    SettingDef("backups.files_keep_daily", _K.INT, 7,
               "Daily snapshots of the configuration, artwork and database dump kept.",
               "backups", min_value=1, max_value=365),
    SettingDef("backups.files_keep_weekly", _K.INT, 4, "Weekly snapshots kept.", "backups",
               min_value=0, max_value=104),
    SettingDef("backups.files_keep_monthly", _K.INT, 6, "Monthly snapshots kept.", "backups",
               min_value=0, max_value=120),
    SettingDef("backups.media_enabled", _K.BOOL, False,
               "Also back up the media libraries (transcoded copies are left out; this can "
               "need as much space as the libraries).", "backups"),
    SettingDef("backups.drill_enabled", _K.BOOL, True,
               "Run the weekly restore drill: the latest backup restored into a throwaway "
               "database and checked.", "backups"),
    SettingDef("backups.drill_weekday", _K.INT, 0,
               "Day of the restore drill (0 Monday ... 6 Sunday).", "backups",
               min_value=0, max_value=6),
    SettingDef("backups.drill_time", _K.STR, "05:00", "Time of the restore drill (HH:MM).",
               "backups", clock_time),
    SettingDef("backups.s3_enabled", _K.BOOL, False,
               "Also copy every backup to an S3-compatible bucket (offsite).", "backups"),
    SettingDef("backups.s3_endpoint", _K.STR, "",
               "The bucket service's address, e.g. https://s3.eu-central-1.amazonaws.com.",
               "backups", optional_http_url),
    SettingDef("backups.s3_region", _K.STR, "", "The bucket's region, if the service needs one.",
               "backups", s3_region),
    SettingDef("backups.s3_bucket", _K.STR, "", "The bucket's name.", "backups", s3_bucket),
    SettingDef("backups.s3_prefix", _K.STR, "smart-iptv", "Folder inside the bucket.",
               "backups", s3_prefix),
    SettingDef("backups.s3_path_style", _K.BOOL, False,
               "Address the bucket by path (MinIO and most self-hosted services) instead of "
               "by host name.", "backups"),
    SettingDef("backups.s3_access_key", _K.STR, "", "The bucket's access key id.", "backups",
               s3_credential, sensitive=True),
    SettingDef("backups.s3_secret_key", _K.STR, "", "The bucket's secret access key.",
               "backups", s3_credential, sensitive=True),
    SettingDef("backups.offsite_interval_minutes", _K.INT, 60,
               "How often the database's backup repository is copied to the bucket, in "
               "minutes (the offsite recovery point).", "backups", min_value=15,
               max_value=1440),
    # --- Personal-data retention (H2, ADR-0020; SPEC §17) ---
    SettingDef("privacy.session_ip_days", _K.INT, 30,
               "Days ended playback sessions keep their IP address and user agent.",
               "privacy", min_value=1, max_value=3650),
    SettingDef("privacy.session_retention_days", _K.INT, 365,
               "Days ended playback sessions are kept.", "privacy", min_value=90,
               max_value=3650),
    SettingDef("privacy.watch_history_days", _K.INT, 365,
               "Days a finished title stays in a customer's watch history.", "privacy",
               min_value=30, max_value=3650),
    SettingDef("privacy.resume_days", _K.INT, 730,
               "Days an unfinished title keeps its resume point.", "privacy", min_value=30,
               max_value=3650),
    SettingDef("privacy.audit_retention_days", _K.INT, 730,
               "Days audit entries are kept (billing entries follow the next setting).",
               "privacy", min_value=365, max_value=3650),
    SettingDef("privacy.audit_billing_retention_days", _K.INT, 2190,
               "Days billing, payment and subscription audit entries are kept (VAT "
               "record-keeping: six years).", "privacy", min_value=1826, max_value=7300),
    SettingDef("privacy.outbox_retention_days", _K.INT, 90,
               "Days sent or failed messages stay in the notification log.", "privacy",
               min_value=7, max_value=3650),
    SettingDef("privacy.auth_log_retention_days", _K.INT, 90,
               "Days failed admin sign-in records (user name and IP) are kept.", "privacy",
               min_value=7, max_value=3650),
    SettingDef("privacy.webhook_payload_days", _K.INT, 365,
               "Days payment providers' raw webhook payloads are kept (the payment records "
               "stay).", "privacy", min_value=30, max_value=3650),
    # --- CrowdSec (H2, ADR-0020): applied by Traefik when CrowdSec is installed ---
    SettingDef("security.crowdsec_enforce", _K.BOOL, True,
               "Block the addresses CrowdSec bans (when CrowdSec is installed on the server).",
               "security"),
    SettingDef("security.crowdsec_allowlist", _K.STR, "",
               "Addresses and networks CrowdSec never blocks, separated by commas, e.g. your "
               "office's 203.0.113.0/24.", "security", network_list),
    # --- end H2 ---
    # --- Application security (M14a, ADR-0021) ---
    SettingDef("security.admin_step_up_window_min", _K.INT, 5,
               "Minutes an admin's confirmation (an authenticator code or a security key) "
               "unlocks sensitive actions such as settings, roles, refunds and deletions.",
               "security", min_value=1, max_value=60),
    SettingDef("security.customer_step_up_window_min", _K.INT, 10,
               "Minutes a customer's password confirmation unlocks adding a TV app or a new "
               "TV app login.", "security", min_value=1, max_value=120),
    SettingDef("security.webauthn_required_for", _K.STR, "none",
               "Which admins must sign in with a security key or passkey instead of an "
               "authenticator code: none, owners, or owners and admins.", "security",
               choices=("none", "owners", "admins")),
    SettingDef("security.admin_ip_allowlist", _K.STR, "",
               "Only these addresses and networks may use the admin, separated by commas "
               "(empty: any). Your current address must be in the list.", "security",
               network_list),
    SettingDef("security.csp_extra_media_origins", _K.STR, "",
               "Extra media and CDN origins the admin and the portal may load media and "
               "images from, separated by commas. Applies after the next update or restart.",
               "security", origin_list),
    SettingDef("security.rate_search_per_minute", _K.INT, 60,
               "Searches one customer may run per minute.", "security", min_value=5,
               max_value=1000),
    SettingDef("security.rate_playback_start_per_minute", _K.INT, 30,
               "Playback starts one customer may request per minute.", "security",
               min_value=5, max_value=600),
    SettingDef("security.rate_device_credentials_per_hour", _K.INT, 10,
               "TV app logins one customer may create or renew per hour.", "security",
               min_value=1, max_value=100),
    SettingDef("security.rate_checkout_per_hour", _K.INT, 10,
               "Checkouts one customer may start per hour.", "security", min_value=1,
               max_value=100),
    SettingDef("security.rate_admin_tmdb_search_per_minute", _K.INT, 30,
               "Manual TMDB searches and re-matches one admin may run per minute.",
               "security", min_value=5, max_value=600),
    SettingDef("security.rate_backup_runs_per_hour", _K.INT, 6,
               "Backups, restore drills and destination checks one admin may start per hour.",
               "security", min_value=1, max_value=60),
    SettingDef("geoip.enabled", _K.BOOL, True,
               "Look up the country and network (ASN) of client addresses with DB-IP Lite.",
               "geoip"),
    SettingDef("geoip.auto_update", _K.BOOL, True,
               "Download the new DB-IP Lite country and ASN databases each month.", "geoip"),
    SettingDef("geoip.unknown_country_policy", _K.STR, "deny",
               "When country rules allow only some countries and an address's country is "
               "unknown: deny or allow playback. Local network addresses are always allowed.",
               "geoip", choices=("deny", "allow")),
    SettingDef("antisharing.enabled", _K.BOOL, True,
               "Score each customer's sign-ins and streams for signs of credential sharing. "
               "Flags never lock an account.", "antisharing"),
    SettingDef("antisharing.max_asns_1h", _K.INT, 2,
               "Networks (ASNs) one customer may use within an hour before it counts as a "
               "sharing signal.", "antisharing", min_value=1, max_value=50),
    SettingDef("antisharing.max_asns_24h", _K.INT, 3,
               "Networks (ASNs) one customer may use within 24 hours before it counts as a "
               "sharing signal.", "antisharing", min_value=1, max_value=100),
    SettingDef("antisharing.max_countries_24h", _K.INT, 1,
               "Countries one customer may appear from within 24 hours before it counts as a "
               "sharing signal.", "antisharing", min_value=1, max_value=20),
    SettingDef("antisharing.max_networks_per_credential_1h", _K.INT, 2,
               "Networks one TV app login may be used from within an hour (changes within "
               "one provider don't count).", "antisharing", min_value=1, max_value=50),
    SettingDef("antisharing.travel_speed_kmh", _K.INT, 900,
               "Faster apparent travel between two countries counts as impossible travel, in "
               "km/h.", "antisharing", min_value=100, max_value=5000),
    SettingDef("antisharing.travel_min_km", _K.INT, 500,
               "Countries closer than this, in km, never count as impossible travel.",
               "antisharing", min_value=0, max_value=5000),
    SettingDef("antisharing.flag_score", _K.INT, 4,
               "Score at which a customer is flagged for review.", "antisharing",
               min_value=1, max_value=100),
    SettingDef("antisharing.notify_admins", _K.BOOL, True,
               "Tell the admins (alert recipients and Telegram) when a customer is flagged.",
               "antisharing"),
    # G-17's byte flagging (ADR-0024): bytes served per session against the title.
    SettingDef("antisharing.bytes_ratio", _K.FLOAT, 4.0,
               "A movie or episode session served more than this many times the bytes its "
               "runtime and rendition bitrate explain counts as a sharing or ripping signal.",
               "antisharing", min_value=1.5, max_value=50.0),
    SettingDef("antisharing.bytes_min_mb", _K.INT, 500,
               "Sessions that were served less than this, in MB, are never checked against "
               "their title.", "antisharing", min_value=16, max_value=100000),
    SettingDef("privacy.antisharing_retention_days", _K.INT, 90,
               "Days the networks and countries seen per customer (anti-sharing evidence) "
               "are kept.", "privacy", min_value=7, max_value=3650),
    # --- end M14a ---
    # --- Scale and CDN (M15, ADR-0022) ---
    SettingDef("edges.stale_after_s", _K.INT, 60,
               "An edge that hasn't reported for this long, in seconds, gets no new "
               "sessions (standalone edges report every 15 s).", "edges", min_value=20,
               max_value=600),
    SettingDef("edges.max_utilisation_pct", _K.INT, 90,
               "An edge sending more than this share of its capacity gets no new sessions, "
               "in percent.", "edges", min_value=10, max_value=100),
    SettingDef("storage.s3_enabled", _K.BOOL, False,
               "Publish the transcoded renditions to an S3-compatible bucket, the origin of "
               "standalone edges and CDNs. Local disk stays the primary copy.", "storage"),
    SettingDef("storage.s3_endpoint", _K.STR, "",
               "The bucket service's address, e.g. https://<account>.r2.cloudflarestorage.com "
               "or https://s3.us-west-004.backblazeb2.com.", "storage", s3_endpoint),
    SettingDef("storage.s3_region", _K.STR, "",
               "The bucket's region (auto for Cloudflare R2; empty means us-east-1).",
               "storage", s3_region),
    SettingDef("storage.s3_bucket", _K.STR, "", "The bucket's name (private).", "storage",
               s3_bucket),
    SettingDef("storage.s3_prefix", _K.STR, "renditions", "Folder inside the bucket.",
               "storage", s3_prefix),
    SettingDef("storage.s3_path_style", _K.BOOL, True,
               "Address the bucket by path (R2, B2, SeaweedFS) instead of by host name.",
               "storage"),
    SettingDef("storage.s3_access_key", _K.STR, "",
               "The publisher's access key id (read and write on the bucket).", "storage",
               s3_credential, sensitive=True),
    SettingDef("storage.s3_secret_key", _K.STR, "", "The publisher's secret access key.",
               "storage", s3_credential, sensitive=True),
    SettingDef("storage.s3_edge_access_key", _K.STR, "",
               "The edges' access key id: read-only on the bucket (sent to standalone edges "
               "over their authenticated control channel).", "storage", s3_credential,
               sensitive=True),
    SettingDef("storage.s3_edge_secret_key", _K.STR, "", "The edges' secret access key.",
               "storage", s3_credential, sensitive=True),
    SettingDef("storage.publish_sources", _K.BOOL, True,
               "Also publish direct-play originals (they can be large); without them those "
               "titles play from this server only.", "storage"),
    SettingDef("storage.publish_mbps", _K.INT, 0,
               "Upload speed limit for publishing, in Mbit/s (0: no limit).", "storage",
               min_value=0, max_value=100000),
    SettingDef("storage.old_generation_hours", _K.INT, 8,
               "Hours a replaced copy stays in the bucket for players still using it (longer "
               "than the longest playback URL lifetime).", "storage", min_value=7,
               max_value=168),
    SettingDef("cdn.bunny_token_key", _K.STR, "",
               "The Bunny pull zone's token authentication key (Security, Token "
               "authentication); CDN edges are used only when it is set.", "cdn",
               s3_credential, sensitive=True),
    SettingDef("cdn.origin_secret", _K.STR, "",
               "The value Bunny sends in the X-Origin-Auth header on its pulls (an edge rule "
               "in the pull zone); our edges serve CDN pulls only with it.", "cdn",
               s3_credential, sensitive=True),
    SettingDef("cdn.bunny_ip_binding", _K.BOOL, False,
               "Sign the viewer's address into Bunny's tokens (IPv6 by its /64).", "cdn"),
    SettingDef("database.replica_reads", _K.BOOL, True,
               "Send reporting reads (dashboard charts, billing figures, the storage report) "
               "to the read replica when one is installed.", "database"),
    SettingDef("database.replica_max_lag_s", _K.INT, 30,
               "The replica is used only while it is at most this many seconds behind.",
               "database", min_value=1, max_value=3600),
    # --- end M15 ---
    # Legal pages (Q1, ADR-0023): Markdown with {service}, {support_email}, {support_phone}
    # and {seller_address} filled in from Branding and Billing.
    SettingDef("legal.privacy_en", _K.STR, legal_template("privacy", "en"),
               "Privacy policy in English (Markdown).", "legal", legal_text, widget="text"),
    SettingDef("legal.privacy_ar", _K.STR, legal_template("privacy", "ar"),
               "Privacy policy in Arabic (Markdown).", "legal", legal_text, widget="text"),
    SettingDef("legal.terms_en", _K.STR, legal_template("terms", "en"),
               "Terms of service in English (Markdown).", "legal", legal_text, widget="text"),
    SettingDef("legal.terms_ar", _K.STR, legal_template("terms", "ar"),
               "Terms of service in Arabic (Markdown).", "legal", legal_text, widget="text"),
)  # fmt: skip


_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")


def _check_extras(definition: SettingDef) -> None:
    """Secrets are text, env names are variable names, widgets are known."""
    problem = ""
    if definition.sensitive and definition.kind is not SettingKind.STR:
        problem = "is sensitive but not a string"
    elif not all(_ENV_NAME.fullmatch(name) for name in definition.env):
        problem = f"names an invalid environment variable in {definition.env}"
    elif definition.env_resolve is not None and not definition.env:
        problem = "has env_resolve without env"
    elif definition.widget is not None and (
        definition.widget not in WIDGETS or definition.kind is not SettingKind.STR
    ):
        problem = f"has an unknown widget {definition.widget}"
    if problem:
        msg = f"Setting {definition.key} {problem}"
        raise ImproperlyConfigured(msg)


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
        _check_extras(definition)
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
