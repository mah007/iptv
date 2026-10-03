"""Strip credentials and tokens from anything that may reach a log line or an audit row.

SPEC §11 redacts credentials in Traefik, Nginx, Alloy and here in Django, where this
module runs as a structlog processor over every event (stdlib records included).
Xtream apps put the password in the URL path, so request paths are the main risk.

Two layers:
- `redact_text` rewrites known secret-bearing patterns inside free text;
- `redact_value` walks structures, masks values under sensitive keys and runs
  `redact_text` on every string.

Masking errs on the side of hiding too much: a log line that loses a harmless
value is a nuisance, one that keeps a password is an incident.
"""

import re
from collections.abc import Mapping, MutableMapping
from typing import Any

MASK = "***"

# Recursion guard for self-referential or absurdly deep structures.
_MAX_DEPTH = 32

# Xtream play URLs: /movie/<user>/<pass>/<id>.<ext> (also series, live, timeshift).
_XTREAM_PATH = re.compile(
    r"/(?P<kind>movie|series|live|timeshift)/[^/?#\s]+/[^/?#\s]+(?=[/?#\s]|$)", re.IGNORECASE
)
# The same, when the path ends in a numeric id (`.../123.mp4`): Django decodes the path, so
# a typed (wrong) password may hold `?`, `#`, spaces or `/`. Everything between the kind
# and the id is masked, wherever the segments seem to end.
_XTREAM_PLAY_PATH = re.compile(
    r"/(?P<kind>movie|series|live|timeshift)/[^\n]+?/"
    r"(?=\d+(?:\.[A-Za-z0-9]{1,5})?(?:[\s\"'?#]|$)"  # the id: `123.mp4`
    r"|\d+/\d{4}-\d{2}-\d{2}:\d{2}-\d{2}/)",  # timeshift: duration/start/
    re.IGNORECASE,
)
# Signed media URLs from the edge: /v/<token>/...
_EDGE_TOKEN = re.compile(r"/v/[^/?#\s]+/")
# Query-string and form parameters that carry credentials (names, not secrets).
_SECRET_PARAMS = "password|pass|username|token|access_token|refresh_token|secret|api_key|key|code"  # noqa: S105
_QUERY_PARAM = re.compile(rf"(?<!\w)(?P<name>{_SECRET_PARAMS})=[^&\s#;\"']+", re.IGNORECASE)
# Real tokens are long; requiring 8+ characters spares prose ("the bearer of ...").
_BEARER = re.compile(r"\b(?P<scheme>bearer)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
# user:password@ in URLs, e.g. redis://:secret@redis-state:6379 in an exception message.
_URL_USERINFO = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/\s:@]*:[^/\s@]+@", re.IGNORECASE)
# "key": "value" and 'key': 'value' pairs inside text (JSON bodies, dict reprs).
_QUOTED_PAIR = re.compile(
    r"""(?P<kq>["'])(?P<key>[^"'\n]{1,64})(?P=kq)(?P<sep>\s*:\s*)"""
    r"""(?P<vq>["'])(?P<value>(?:\\.|(?!(?P=vq)).)*)(?P=vq)"""
)

# Mapping keys whose values are always masked. Substring match, case-insensitive,
# so `new_password`, `HTTP_AUTHORIZATION`, `csrftoken` and `secret_encrypted` count.
# `otp` must start a word segment so `footprint` is spared while `otpauth_uri` is not.
# Identifiers such as `username` stay readable in structured data (audit diffs need
# them); in free text they are masked as query/form parameters instead.
_SENSITIVE_KEY = re.compile(
    r"password|passwd|secret|token|authorization|cookie|api_key|(?:^|[^a-z])otp|totp"
    r"|credential|mfa_code|sessionid",
    re.IGNORECASE,
)
_SECRET_PARAM_NAME = re.compile(rf"(?:{_SECRET_PARAMS})", re.IGNORECASE)

# structlog bookkeeping that must reach the renderer untouched.
_STRUCTLOG_META = frozenset({"_record", "_from_structlog"})


def is_sensitive_key(key: str) -> bool:
    """True when values stored under this mapping key must never be logged or audited."""
    return bool(_SENSITIVE_KEY.search(key))


def _mask_quoted_pair(match: re.Match[str]) -> str:
    # Quoted pairs in text are usually request bodies, so form-parameter names count too.
    key = match["key"]
    if not (is_sensitive_key(key) or _SECRET_PARAM_NAME.fullmatch(key)):
        return match[0]
    kq, vq = match["kq"], match["vq"]
    return f"{kq}{match['key']}{kq}{match['sep']}{vq}{MASK}{vq}"


def redact_text(text: str) -> str:
    """Mask credentials and tokens that appear inside free text such as paths or messages."""
    text = _XTREAM_PLAY_PATH.sub(lambda m: f"/{m['kind']}/{MASK}/{MASK}/", text)
    text = _XTREAM_PATH.sub(lambda m: f"/{m['kind']}/{MASK}/{MASK}", text)
    text = _EDGE_TOKEN.sub(f"/v/{MASK}/", text)
    text = _URL_USERINFO.sub(lambda m: f"{m['scheme']}{MASK}:{MASK}@", text)
    text = _BEARER.sub(lambda m: f"{m['scheme']} {MASK}", text)
    text = _QUERY_PARAM.sub(lambda m: f"{m['name']}={MASK}", text)
    return _QUOTED_PAIR.sub(_mask_quoted_pair, text)


def redact_value(value: Any, *, _depth: int = 0) -> Any:
    """Return a redacted copy of `value`; the input is never modified.

    Mappings keep their keys and mask sensitive ones; lists, tuples and sets are
    walked (sets come back as lists); strings and bytes go through `redact_text`.
    Other scalars (numbers, booleans, None, dates, UUIDs) pass through.
    """
    if _depth > _MAX_DEPTH:
        return MASK
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            key: (
                MASK
                if isinstance(key, str) and is_sensitive_key(key)
                else redact_value(item, _depth=_depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, tuple | list | set | frozenset):
        items = [redact_value(item, _depth=_depth + 1) for item in value]
        return tuple(items) if isinstance(value, tuple) else items
    return value


def redact_event_dict(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: redact every value of the event, including the message."""
    for key, value in list(event_dict.items()):
        if key in _STRUCTLOG_META:
            continue
        event_dict[key] = MASK if is_sensitive_key(key) else redact_value(value)
    return event_dict
