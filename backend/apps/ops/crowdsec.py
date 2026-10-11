"""CrowdSec's bouncer in Traefik, configured from the admin (ADR-0020).

When CrowdSec is installed (`CROWDSEC=1`, docker/compose.crowdsec.yml), Traefik puts the
`crowdsec` middleware (the CrowdSec bouncer plugin) in front of every router of its
public entry point. Its settings come from here: the worker renders
`crowdsec.yml` into a volume that Traefik reads (read-only), so the admin's
**Settings → Security** switches enforcement on or off and lists addresses that are
never blocked, without a restart.

The file always defines the middleware, `enabled: false` included (the plugin then lets
everything through), so no router ever points at a missing middleware. When web or the
worker is down, Traefik keeps the last file it read. The bouncer fails open: if the
CrowdSec engine is unreachable, requests pass.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Final

from django.conf import settings

from apps.core.services import get_setting

logger = logging.getLogger(__name__)

FILE_NAME: Final = "crowdsec.yml"
#: Where the crowdsec container's local API answers on the edge network.
LAPI_HOST: Final = "crowdsec:8080"
#: The bouncer key, a Docker secret mounted into Traefik by the overlay.
KEY_FILE: Final = "/run/secrets/crowdsec_bouncer_key"


def allowlist() -> list[str]:
    raw = str(get_setting("security.crowdsec_allowlist"))
    return [item.strip() for item in raw.split(",") if item.strip()]


def middleware(*, enabled: bool, trusted: list[str]) -> dict[str, Any]:
    """The plugin's options (maxlerebourg/crowdsec-bouncer-traefik-plugin, v1.7)."""
    return {
        "http": {
            "middlewares": {
                "crowdsec": {
                    "plugin": {
                        "crowdsec": {
                            "enabled": enabled,
                            "logLevel": "INFO",
                            # Stream mode: the bans are synced from the local API and checked
                            # in memory; Traefik never waits for CrowdSec on a request.
                            "crowdsecMode": "stream",
                            "updateIntervalSeconds": 15,
                            # Fail open: an unreachable engine blocks nobody.
                            "updateMaxFailure": -1,
                            "streamStartupBlock": False,
                            "crowdsecLapiScheme": "http",
                            "crowdsecLapiHost": LAPI_HOST,
                            "crowdsecLapiKeyFile": KEY_FILE,
                            "remediationStatusCode": 403,
                            "clientTrustedIPs": trusted,
                            "metricsUpdateIntervalSeconds": 600,
                        }
                    }
                }
            }
        }
    }


def render() -> str:
    config = middleware(enabled=bool(get_setting("security.crowdsec_enforce")), trusted=allowlist())
    # JSON is YAML: Traefik's file provider reads it as is.
    return (
        "# Written by Smart IPTV (apps/ops/crowdsec.py) from Settings → Security;"
        " changes here are overwritten.\n" + json.dumps(config, indent=2) + "\n"
    )


def directory() -> Path | None:
    raw = str(getattr(settings, "CROWDSEC_TRAEFIK_DIR", ""))
    return Path(raw) if raw else None


def write(target: Path | None = None) -> bool:
    """Write the file when it changed; True when it did. Atomic: Traefik never reads half."""
    folder = target or directory()
    if folder is None or not folder.is_dir():
        return False
    path = folder / FILE_NAME
    text = render()
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    temporary = folder / f".{FILE_NAME}.tmp"
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, 0o644)
    temporary.replace(path)
    logger.info("crowdsec.config_written", extra={"enabled": '"enabled": true' in text})
    return True
