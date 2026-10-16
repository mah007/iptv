"""What each kind of import source holds, and the rclone remote it becomes (ADR-0025).

A source has non-secret `options` (validated here, shown in the admin) and secrets
(encrypted on the model, write-only in the API). `rclone_section` turns both into the
`[src]` section of a per-run rclone config file, which `apps.imports.rclone` writes to a
private temporary folder: credentials never reach rclone's command line or a log.

Every host or URL an admin types goes through the egress guard of `apps.live.egress`
(statically when saved, with DNS before each run): no loopback, link-local or cloud
metadata addresses, and none of the stack's own services.
"""

import json
import re
from collections.abc import Callable, Mapping
from pathlib import PurePosixPath
from typing import Any, Final
from urllib.parse import urlsplit

from apps.imports.models import OAUTH_KINDS, SourceKind
from apps.live import egress

#: The rclone config section every run uses (`src:` in remote paths).
REMOTE: Final = "src"


class SourceError(ValueError):
    """A source's options or secrets are not usable; `field` names the culprit."""

    def __init__(self, field: str, code: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.code = code
        self.message = message


# --- S3-compatible providers ---------------------------------------------------------------------

#: Our provider names -> rclone's `provider`. Backblaze B2 is used through its
#: S3-compatible API (rclone has no B2 provider for its S3 backend).
S3_PROVIDERS: Final = {
    "aws": "AWS",
    "b2": "Other",
    "r2": "Cloudflare",
    "wasabi": "Wasabi",
    "minio": "Minio",
    "other": "Other",
}
WEBDAV_VENDORS: Final = ("other", "nextcloud", "owncloud", "sharepoint", "sharepoint-ntlm")
ONEDRIVE_TYPES: Final = ("personal", "business", "documentLibrary")

_REGION = re.compile(r"^[a-z0-9-]{1,40}$")
_HOSTNAME = re.compile(r"^[A-Za-z0-9.-]{1,253}$|^\[?[0-9A-Fa-f:.]+\]?$")
_USERNAME = re.compile(r"^[^\s:@/\\]{1,128}$")
_PRINTABLE = re.compile(r"^[^\x00-\x1f\x7f]*$")
_DRIVE_ID = re.compile(r"^[A-Za-z0-9!_.\-]{1,200}$")
_HOST_KEY = re.compile(r"^(ssh-[a-z0-9-]+|ecdsa-sha2-[a-z0-9-]+|sk-[a-z0-9@.-]+) [A-Za-z0-9+/=]+$")


#: The non-secret options of each kind (all strings unless noted), and their secrets.
OPTIONS: Final[dict[str, tuple[str, ...]]] = {
    SourceKind.S3: ("provider", "endpoint", "region", "path_style", "root"),
    SourceKind.SFTP: ("host", "port", "username", "host_keys", "root"),
    SourceKind.WEBDAV: ("url", "vendor", "username", "root"),
    SourceKind.DRIVE: ("root_folder_id", "client_id", "root"),
    SourceKind.DROPBOX: ("client_id", "root"),
    SourceKind.ONEDRIVE: ("drive_id", "drive_type", "client_id", "root"),
    SourceKind.XTREAM: ("url",),
}
SECRETS: Final[dict[str, tuple[str, ...]]] = {
    SourceKind.S3: ("access_key_id", "secret_access_key"),
    SourceKind.SFTP: ("password", "private_key", "key_passphrase"),
    SourceKind.WEBDAV: ("password",),
    SourceKind.DRIVE: ("token", "client_secret"),
    SourceKind.DROPBOX: ("token", "client_secret"),
    SourceKind.ONEDRIVE: ("token", "client_secret"),
    SourceKind.XTREAM: ("account_username", "password"),
}
ALL_SECRETS: Final = tuple(dict.fromkeys(name for names in SECRETS.values() for name in names))
ALL_OPTIONS: Final = tuple(dict.fromkeys(name for names in OPTIONS.values() for name in names))


def _text(value: Any, field: str, *, limit: int = 500) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SourceError(field, "invalid", "Expected text.")
    text = value.strip()
    if len(text) > limit:
        raise SourceError(field, "too_long", f"At most {limit} characters.")
    if not _PRINTABLE.match(text):
        raise SourceError(field, "invalid", "Line breaks and control characters aren't allowed.")
    return text


def clean_root(value: Any, field: str = "root") -> str:
    """A path inside the remote: relative, without `..`, `.` or empty segments."""
    text = _text(value, field, limit=1000).strip("/")
    if not text:
        return ""
    parts = PurePosixPath(text).parts
    if any(part in {".", ".."} for part in parts) or "\\" in text:
        raise SourceError(field, "invalid_path", "Use a plain folder path without . or ..")
    return "/".join(parts)


def _http_url(value: Any, field: str) -> str:
    text = _text(value, field)
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise SourceError(field, "invalid_url", "Use an http:// or https:// address.")
    if parts.username or parts.password:
        raise SourceError(
            field, "credentials_in_url", "Put the user name and password in their fields."
        )
    try:
        egress.check_url_static(text)
    except egress.UnsafeDestination as exc:
        raise SourceError(field, "unsafe_destination", f"Not allowed: {exc.reason}.") from None
    return text.rstrip("/")


def _host(value: Any, field: str) -> str:
    text = _text(value, field, limit=253)
    if not text or not _HOSTNAME.match(text):
        raise SourceError(field, "invalid_host", "Use a host name or an IP address.")
    try:
        egress.check_url_static(f"sftp://{text}")
    except egress.UnsafeDestination as exc:
        raise SourceError(field, "unsafe_destination", f"Not allowed: {exc.reason}.") from None
    return text.lower()


def _host_keys(value: Any) -> str:
    """OpenSSH public host keys ("ssh-ed25519 AAAA...", or ssh-keyscan lines with the host
    in front), as rclone's comma-separated `host_keys`."""
    text = _text(
        value.replace("\n", ",") if isinstance(value, str) else value, "host_keys", limit=8000
    )
    keys: list[str] = []
    for raw in text.split(","):
        words = raw.split()
        if not words:
            continue
        if len(words) >= 3 and not _HOST_KEY.match(" ".join(words[:2])):
            words = words[1:3]  # "host algo key" from ssh-keyscan
        entry = " ".join(words[:2])
        if not _HOST_KEY.match(entry):
            raise SourceError(
                "host_keys", "invalid_host_key", "Paste the server's public host key."
            )
        keys.append(entry)
    if len(keys) > 16:
        raise SourceError("host_keys", "invalid_host_key", "At most 16 host keys.")
    return ",".join(dict.fromkeys(keys))


def _choice(value: Any, field: str, choices: tuple[str, ...], default: str) -> str:
    text = _text(value, field) or default
    if text not in choices:
        raise SourceError(field, "invalid_choice", f"Choose one of: {', '.join(choices)}.")
    return text


def _bool(value: Any, field: str, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    raise SourceError(field, "invalid", "Expected true or false.")


def _port(value: Any) -> int:
    if value in (None, ""):
        return 22
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise SourceError("port", "invalid_port", "Use a port from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise SourceError("port", "invalid_port", "Use a port from 1 to 65535.")
    return port


def _s3(options: Mapping[str, Any]) -> dict[str, Any]:
    provider = _choice(options.get("provider"), "provider", tuple(S3_PROVIDERS), "aws")
    endpoint = options.get("endpoint") or ""
    region = _text(options.get("region"), "region", limit=40)
    if region and not _REGION.match(region):
        raise SourceError("region", "invalid_region", "Use a region name such as us-east-1.")
    if provider != "aws" and not endpoint:
        raise SourceError("endpoint", "required", "This provider needs its endpoint address.")
    return {
        "provider": provider,
        "endpoint": _http_url(endpoint, "endpoint") if endpoint else "",
        "region": region,
        "path_style": _bool(
            options.get("path_style"), "path_style", provider in {"minio", "other"}
        ),
        "root": clean_root(options.get("root")),
    }


def _sftp(options: Mapping[str, Any]) -> dict[str, Any]:
    username = _text(options.get("username"), "username", limit=128)
    if not username or not _USERNAME.match(username):
        raise SourceError("username", "required", "Enter the user name.")
    return {
        "host": _host(options.get("host"), "host"),
        "port": _port(options.get("port")),
        "username": username,
        "host_keys": _host_keys(options.get("host_keys") or ""),
        "root": clean_root(options.get("root")),
    }


def _webdav(options: Mapping[str, Any]) -> dict[str, Any]:
    if not options.get("url"):
        raise SourceError("url", "required", "Enter the WebDAV address.")
    username = _text(options.get("username"), "username", limit=128)
    return {
        "url": _http_url(options.get("url"), "url"),
        "vendor": _choice(options.get("vendor"), "vendor", WEBDAV_VENDORS, "other"),
        "username": username,
        "root": clean_root(options.get("root")),
    }


def _xtream(options: Mapping[str, Any]) -> dict[str, Any]:
    if not options.get("url"):
        raise SourceError("url", "required", "Enter the IPTV server address.")
    url = _http_url(options.get("url"), "url")
    parts = urlsplit(url)
    if parts.query or parts.fragment:
        raise SourceError(
            "url", "invalid_url", "Use the server address without a query or fragment."
        )
    if parts.path.rstrip("/").endswith("/player_api.php"):
        raise SourceError("url", "invalid_url", "Use the server address without player_api.php.")
    return {"url": url}


def _oauth(kind: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    def clean(options: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "client_id": _text(options.get("client_id"), "client_id", limit=200),
            "root": clean_root(options.get("root")),
        }
        if kind == SourceKind.DRIVE:
            folder = _text(options.get("root_folder_id"), "root_folder_id", limit=200)
            if folder and not _DRIVE_ID.match(folder):
                raise SourceError("root_folder_id", "invalid", "Paste the folder's id only.")
            result["root_folder_id"] = folder
        if kind == SourceKind.ONEDRIVE:
            drive_id = _text(options.get("drive_id"), "drive_id", limit=200)
            if drive_id and not _DRIVE_ID.match(drive_id):
                raise SourceError("drive_id", "invalid", "Paste the drive id only.")
            drive_type = _text(options.get("drive_type"), "drive_type", limit=40)
            if drive_type and drive_type not in ONEDRIVE_TYPES:
                raise SourceError(
                    "drive_type", "invalid_choice", f"Choose one of: {', '.join(ONEDRIVE_TYPES)}."
                )
            result["drive_id"] = drive_id
            result["drive_type"] = drive_type
        return result

    return clean


_CLEANERS: Final[dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]]] = {
    SourceKind.S3: _s3,
    SourceKind.SFTP: _sftp,
    SourceKind.WEBDAV: _webdav,
    SourceKind.DRIVE: _oauth(SourceKind.DRIVE),
    SourceKind.DROPBOX: _oauth(SourceKind.DROPBOX),
    SourceKind.ONEDRIVE: _oauth(SourceKind.ONEDRIVE),
    SourceKind.XTREAM: _xtream,
}


def clean_options(kind: str, options: Mapping[str, Any]) -> dict[str, Any]:
    """The kind's options, validated and normalised; unknown names are refused."""
    if kind not in _CLEANERS:
        raise SourceError("kind", "invalid_choice", "Unknown kind of source.")
    unknown = sorted(set(options) - set(OPTIONS[kind]))
    if unknown:
        raise SourceError(unknown[0], "unknown_option", "Not an option of this kind of source.")
    return _CLEANERS[kind](options)


def token_json(value: str) -> str:
    """The OAuth token `rclone authorize` prints, as compact JSON on one line."""
    text = value.strip()
    if text.startswith("Paste the following"):  # the whole message was copied
        text = text.split("\n", 1)[-1].strip()
    text = text.removeprefix("--->").removesuffix("<---End paste").strip()
    try:
        data = json.loads(text)
    except ValueError:
        raise SourceError(
            "token", "invalid_token", "Paste the JSON rclone authorize printed."
        ) from None
    if not isinstance(data, dict) or not (data.get("access_token") or data.get("refresh_token")):
        raise SourceError("token", "invalid_token", "Paste the JSON rclone authorize printed.")
    return json.dumps(data, separators=(",", ":"))


def private_key(value: str) -> str:
    text = value.strip().replace("\r\n", "\n")
    if not (text.startswith("-----BEGIN ") and "PRIVATE KEY-----" in text):
        raise SourceError("private_key", "invalid_key", "Paste a PEM or OpenSSH private key.")
    return text + "\n"


def clean_secrets(kind: str, values: Mapping[str, str]) -> dict[str, str]:
    """The given secrets of a kind, normalised (empty values are dropped by the caller)."""
    unknown = sorted(set(values) - set(SECRETS.get(kind, ())))
    if unknown:
        raise SourceError(unknown[0], "unknown_option", "Not a secret of this kind of source.")
    cleaned: dict[str, str] = {}
    for name, value in values.items():
        if not value:
            continue
        if name == "token":
            cleaned[name] = token_json(value)
        elif name == "private_key":
            cleaned[name] = private_key(value)
        else:
            cleaned[name] = _text(value, name, limit=2000)
    return cleaned


def missing_secrets(kind: str, secrets: Mapping[str, str]) -> list[str]:
    """Secrets a usable source of this kind still lacks."""
    if kind == SourceKind.S3:
        return [name for name in ("access_key_id", "secret_access_key") if not secrets.get(name)]
    if kind == SourceKind.SFTP:
        return [] if secrets.get("password") or secrets.get("private_key") else ["password"]
    if kind in OAUTH_KINDS:
        return [] if secrets.get("token") else ["token"]
    if kind == SourceKind.XTREAM:
        return [name for name in ("account_username", "password") if not secrets.get(name)]
    return []


def hosts(kind: str, options: Mapping[str, Any]) -> list[tuple[str, int | None]]:
    """The hosts (and ports) this source connects to that an admin typed."""
    if kind == SourceKind.SFTP:
        return [(str(options["host"]), int(options.get("port") or 22))]
    url = options.get("endpoint") if kind == SourceKind.S3 else options.get("url")
    if kind in {SourceKind.S3, SourceKind.WEBDAV, SourceKind.XTREAM} and url:
        parts = urlsplit(str(url))
        return [(parts.hostname or "", parts.port)]
    return []


def summary(kind: str, options: Mapping[str, Any]) -> str:
    """One line the admin recognises the source by (no secrets)."""
    if kind == SourceKind.S3:
        where = urlsplit(str(options.get("endpoint") or "")).hostname or "AWS"
        return f"{where}/{options.get('root') or ''}".rstrip("/")
    if kind == SourceKind.SFTP:
        return f"{options.get('username')}@{options.get('host')}:{options.get('port') or 22}"
    if kind == SourceKind.WEBDAV:
        return str(options.get("url") or "")
    if kind == SourceKind.XTREAM:
        return urlsplit(str(options.get("url") or "")).netloc
    return str(options.get("root") or "/")


# --- rclone ------------------------------------------------------------------------------------


type Obscure = Callable[[str], str]


def _s3_section(options: Mapping[str, Any], secrets: Mapping[str, str]) -> dict[str, str]:
    values = {
        "type": "s3",
        "provider": S3_PROVIDERS[str(options.get("provider") or "aws")],
        "access_key_id": secrets.get("access_key_id", ""),
        "secret_access_key": secrets.get("secret_access_key", ""),
        "env_auth": "false",
        "no_check_bucket": "true",
    }
    if options.get("region"):
        values["region"] = str(options["region"])
    if options.get("endpoint"):
        values["endpoint"] = str(options["endpoint"])
    if options.get("path_style"):
        values["force_path_style"] = "true"
    return values


def _sftp_section(
    options: Mapping[str, Any], secrets: Mapping[str, str], obscure: Obscure, *, pin: bool
) -> dict[str, str]:
    values = {
        "type": "sftp",
        "host": str(options["host"]),
        "port": str(options.get("port") or 22),
        "user": str(options["username"]),
    }
    if secrets.get("password"):
        values["pass"] = obscure(secrets["password"])
    if secrets.get("private_key"):
        # rclone wants the PEM on one line, its line breaks written as \n.
        values["key_pem"] = secrets["private_key"].rstrip("\n").replace("\n", "\\n")
        if secrets.get("key_passphrase"):
            values["key_file_pass"] = obscure(secrets["key_passphrase"])
    if options.get("host_keys"):
        values["host_keys"] = str(options["host_keys"])
    elif pin:
        values["pin_host_key"] = "true"
    return values


def _webdav_section(
    options: Mapping[str, Any], secrets: Mapping[str, str], obscure: Obscure
) -> dict[str, str]:
    values = {"type": "webdav", "url": str(options["url"]), "vendor": str(options["vendor"])}
    if options.get("username"):
        values["user"] = str(options["username"])
    if secrets.get("password"):
        values["pass"] = obscure(secrets["password"])
    return values


def _oauth_section(
    kind: str, options: Mapping[str, Any], secrets: Mapping[str, str]
) -> dict[str, str]:
    values = {"type": str(kind), "token": secrets.get("token", "")}
    if kind == SourceKind.DRIVE:
        values["scope"] = "drive.readonly"
    names: dict[str, tuple[str, ...]] = {
        SourceKind.DRIVE: ("root_folder_id",),
        SourceKind.ONEDRIVE: ("drive_id", "drive_type"),
    }
    for name in (*names.get(kind, ()), "client_id"):
        if options.get(name):
            values[name] = str(options[name])
    if secrets.get("client_secret"):
        values["client_secret"] = secrets["client_secret"]
    return values


def rclone_section(
    kind: str,
    options: Mapping[str, Any],
    secrets: Mapping[str, str],
    *,
    obscure: Obscure,
    pin_host_key: bool = False,
) -> dict[str, str]:
    """The rclone options of the `[src]` remote. `obscure` is `rclone obscure` (rclone
    wants passwords in that reversible form)."""
    if kind == SourceKind.S3:
        values = _s3_section(options, secrets)
    elif kind == SourceKind.SFTP:
        values = _sftp_section(options, secrets, obscure, pin=pin_host_key)
    elif kind == SourceKind.WEBDAV:
        values = _webdav_section(options, secrets, obscure)
    elif kind in OAUTH_KINDS:
        values = _oauth_section(kind, options, secrets)
    else:
        raise SourceError("kind", "invalid_choice", "This source does not use rclone.")
    for name, value in values.items():
        if "\n" in value or "\r" in value:
            msg = f"rclone option {name} has a line break"
            raise SourceError(name, "invalid", msg)
    return values


def remote_path(root: str, path: str = "") -> str:
    """`src:<root>/<path>` for rclone; `path` is relative to the source's root."""
    joined = "/".join(part for part in (root.strip("/"), clean_root(path, "path")) if part)
    return f"{REMOTE}:{joined}"
