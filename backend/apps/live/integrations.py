"""Syncing channels from the operator's own ErsatzTV or MediaMTX (ADR-0017).

Both are separate processes the operator runs: ErsatzTV builds 24/7 channels from
their own library; MediaMTX receives their own encoders and cameras. A sync reads
the instance's own API and builds every stream URL on the configured instance, so
it can never import a third-party playlist (SPEC §1.1):

- ErsatzTV: `GET {base}/api/channels` (id, number, name). Streams are
  `{base}/iptv/channel/{number}.ts`, the guide `{base}/iptv/xmltv.xml`, whose
  channel ids start with `C{number}.`. An access token, when set, is appended as
  `access_token` (ErsatzTV's own scheme).
- MediaMTX: `GET {base}/v3/paths/list` (basic auth). Streams are
  `{stream_base}/{path}` (RTSP), read with the same user.

New channels arrive **disabled**, with the integration's rights holder and licence
reference; the admin reviews and enables them. Channels the instance no longer has
are disabled and reported, never deleted.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx
import structlog
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts import crypto
from apps.audit import services as audit
from apps.catalog.models import Category, CategoryKind
from apps.core.redaction import redact_text
from apps.live import egress, epg, sources
from apps.live.models import (
    ChannelOrigin,
    EpgChannel,
    EpgSource,
    EpgSourceKind,
    IntegrationKind,
    LiveChannel,
    LiveIntegration,
)
from apps.xtream_api.cache import invalidate_on_commit

logger = structlog.get_logger(__name__)

TIMEOUT_S = 20.0
MAX_CHANNELS = 2000
_MEDIAMTX_PATH = re.compile(r"[A-Za-z0-9_./~-]{1,200}")
_NUMBER = re.compile(r"[0-9]{1,6}(?:\.[0-9]{1,4})?")


class IntegrationError(Exception):
    """The instance could not be read (the message never holds a credential)."""


@dataclass(frozen=True, slots=True)
class RemoteChannel:
    ref: str
    name: str
    source_url: str
    xmltv_prefix: str = ""


@dataclass(slots=True)
class SyncResult:
    created: int = 0
    updated: int = 0
    missing: int = 0
    total: int = 0
    guide: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "updated": self.updated,
            "missing": self.missing,
            "total": self.total,
            "guide": self.guide,
        }


def credentials(integration: LiveIntegration) -> dict[str, str]:
    if not integration.credentials_encrypted:
        return {}
    try:
        data = json.loads(crypto.decrypt(integration.credentials_encrypted))
    except (crypto.DecryptionError, ValueError):
        return {}
    return {key: str(value) for key, value in data.items() if isinstance(value, str) and value}


def encrypt_credentials(values: dict[str, str]) -> str:
    kept = {key: value for key, value in values.items() if value}
    return crypto.encrypt(json.dumps(kept)) if kept else ""


def _base(url: str) -> str:
    return sources.validate_http_url(url).rstrip("/")


def _with_token(url: str, token: str) -> str:
    if not token:
        return url
    parts = urlsplit(url)
    query = f"{parts.query}&" if parts.query else ""
    return urlunsplit(parts._replace(query=query + urlencode({"access_token": token})))


def _get_json(client: httpx.Client, url: str, **kwargs: Any) -> Any:
    redactor = sources.Redactor(url)
    try:
        response = client.get(url, **kwargs)
    except egress.UnsafeDestination as refused:
        raise IntegrationError(f"the instance's address is refused ({refused.reason})") from None
    except httpx.HTTPError as exc:
        raise IntegrationError(redactor(f"the request failed ({type(exc).__name__})")) from None
    if response.status_code != 200:
        raise IntegrationError(f"the instance answered HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise IntegrationError("the instance did not answer JSON") from None


def ersatztv_channels(integration: LiveIntegration, client: httpx.Client) -> list[RemoteChannel]:
    base = _base(integration.base_url)
    secrets = credentials(integration)
    token = secrets.get("access_token", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = _get_json(client, f"{base}/api/channels", headers=headers)
    items = data if isinstance(data, list) else []
    found = []
    for item in items[:MAX_CHANNELS]:
        if not isinstance(item, dict):
            continue
        number, name = str(item.get("number", "")).strip(), str(item.get("name", "")).strip()
        if not _NUMBER.fullmatch(number) or not name:
            continue
        url = _with_token(f"{base}/iptv/channel/{quote(number)}.ts", token)
        found.append(
            RemoteChannel(ref=number, name=name[:255], source_url=url, xmltv_prefix=f"C{number}.")
        )
    return found


def mediamtx_channels(integration: LiveIntegration, client: httpx.Client) -> list[RemoteChannel]:
    base = _base(integration.base_url)
    secrets = credentials(integration)
    auth = (secrets["username"], secrets.get("password", "")) if secrets.get("username") else None
    data = _get_json(
        client, f"{base}/v3/paths/list", params={"itemsPerPage": MAX_CHANNELS}, auth=auth
    )
    items = data.get("items", []) if isinstance(data, dict) else []
    stream = urlsplit(sources.validate_source_url(integration.stream_base_url))
    if stream.scheme not in ("rtsp", "rtsps", "rtmp", "rtmps", "srt", "http", "https"):
        raise IntegrationError("the stream base URL is not usable")
    userinfo = ""
    if auth is not None:
        userinfo = f"{quote(auth[0], safe='')}:{quote(auth[1], safe='')}@"
    netloc = userinfo + (stream.netloc.rsplit("@", 1)[-1])
    found = []
    for item in items[:MAX_CHANNELS]:
        name = str(item.get("name", "")).strip() if isinstance(item, dict) else ""
        if not _MEDIAMTX_PATH.fullmatch(name) or ".." in name:
            continue
        path = f"{stream.path.rstrip('/')}/{name}"
        url = urlunsplit((stream.scheme, netloc, path, "", ""))
        found.append(RemoteChannel(ref=name[:128], name=name[:255], source_url=url))
    return found


FETCHERS: dict[str, Callable[[LiveIntegration, httpx.Client], list[RemoteChannel]]] = {
    IntegrationKind.ERSATZTV: ersatztv_channels,
    IntegrationKind.MEDIAMTX: mediamtx_channels,
}


def _group(integration: LiveIntegration) -> Category:
    if integration.group_id is not None and integration.group is not None:
        return integration.group
    slug = (slugify(integration.name) or integration.kind)[:90]
    group, _ = Category.objects.get_or_create(
        kind=CategoryKind.LIVE,
        slug=slug,
        defaults={"name_en": integration.name[:100], "name_ar": integration.name[:100]},
    )
    integration.group = group
    integration.save(update_fields=["group", "updated_at"])
    return group


def _apply(integration: LiveIntegration, remote: list[RemoteChannel], result: SyncResult) -> None:
    group = _group(integration)
    existing = {channel.origin_ref: channel for channel in integration.channels.all()}
    seen: set[str] = set()
    for item in remote:
        seen.add(item.ref)
        channel = existing.get(item.ref)
        encrypted = sources.encrypt(item.source_url)
        if channel is None:
            channel = LiveChannel.objects.create(
                name=item.name,
                group=group,
                source_encrypted=encrypted,
                enabled=False,
                rights_holder=integration.rights_holder,
                license_ref=integration.license_ref,
                origin=ChannelOrigin(integration.kind),
                origin_ref=item.ref,
                integration=integration,
                sort=(result.total + 1) * 10,
            )
            result.created += 1
            audit.record(
                "live.channel.sync_create",
                actor=None,
                target=channel,
                after={"name": channel.name, "integration": str(integration.pk)},
            )
        else:
            changed = channel.name != item.name
            try:
                same_source = sources.decrypt(channel.source_encrypted) == item.source_url
            except crypto.DecryptionError:
                same_source = False
            if changed or not same_source:
                channel.name = item.name
                if not same_source:
                    channel.source_encrypted, channel.probe = encrypted, {}
                channel.save(update_fields=["name", "source_encrypted", "probe", "updated_at"])
                result.updated += 1
        result.total += 1
    gone = [channel for ref, channel in existing.items() if ref not in seen and channel.enabled]
    for channel in gone:
        channel.enabled = False
        channel.save(update_fields=["enabled", "updated_at"])
    result.missing = len([ref for ref in existing if ref not in seen])


def _guide_source(integration: LiveIntegration) -> EpgSource:
    url = _with_token(
        f"{_base(integration.base_url)}/iptv/xmltv.xml",
        credentials(integration).get("access_token", ""),
    )
    source = integration.epg_sources.first()
    if source is None:
        source = EpgSource(
            name=f"{integration.name} (ErsatzTV)"[:100],
            kind=EpgSourceKind.URL,
            integration=integration,
        )
    source.url_encrypted = sources.encrypt(url)
    source.save()
    return source


def _map_guide(integration: LiveIntegration, source: EpgSource) -> int:
    """Give ErsatzTV channels their XMLTV ids (`C{number}.…`) from the imported guide."""
    ids = list(EpgChannel.objects.filter(source=source).values_list("xmltv_id", flat=True))
    mapped = 0
    for channel in integration.channels.filter(origin=ChannelOrigin.ERSATZTV):
        prefix = f"C{channel.origin_ref}."
        match = next((value for value in ids if value.startswith(prefix)), "")
        if match and channel.epg_channel_id != match:
            channel.epg_channel_id, channel.epg_source = match, source
            channel.save(update_fields=["epg_channel_id", "epg_source", "updated_at"])
            mapped += 1
    return mapped


def sync(integration: LiveIntegration, *, client: httpx.Client | None = None) -> SyncResult:
    """Read the instance and create, update or disable its channels; records the outcome."""
    result = SyncResult()
    http = client or egress.guarded_client(timeout=TIMEOUT_S, follow_redirects=False)
    try:
        remote = FETCHERS[integration.kind](integration, http)
        with transaction.atomic():
            _apply(integration, remote, result)
            invalidate_on_commit()
        if integration.kind == IntegrationKind.ERSATZTV:
            source = _guide_source(integration)
            epg.import_source(source, client=http)  # the guide's channels
            if _map_guide(integration, source):
                epg.import_source(source, client=http)  # now with their programmes
            result.guide = True
    except (IntegrationError, ValueError, ValidationError) as exc:
        detail = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
        message = redact_text(detail)[:500]
        integration.last_error = message
        result.errors.append(message)
    else:
        integration.last_error = ""
    finally:
        if client is None:
            http.close()
    integration.last_sync_at = timezone.now()
    integration.last_result = result.as_dict()
    integration.save(update_fields=["last_sync_at", "last_error", "last_result", "updated_at"])
    logger.info("live.integration_synced", integration=str(integration.pk), **result.as_dict())
    return result
