"""Starting playback from an Xtream play URL (SPEC §7.4, §7.5).

`/movie|series/{u}/{p}/{xc_id}.{ext}` authenticates, resolves the title and asks
a `PlaybackStarter` for a signed edge URL, then answers 302. The starter runs the
entitlement checks in SPEC §7.4 order and takes the concurrency slot; it answers
with the URL or a stable error code. XtreamApiConfig.ready() installs the one over
`playback.services.start_playback` (starter.py); `UnavailablePlayback` (503
PLAYBACK_UNAVAILABLE) is the default before that.

Responses to apps carry no body: players only look at the status. `X-Reason`
names the refusal for support and debugging.
"""

from dataclasses import dataclass
from typing import Protocol

from apps.accounts.models import Device, User
from apps.core.errors import ErrorCode
from apps.xtream_api.dto import TitleRef

PLAYBACK_UNAVAILABLE = "PLAYBACK_UNAVAILABLE"
DEFAULT_RETRY_AFTER_S = 60

#: Refusal code → HTTP status. Unknown codes are 403: a refusal never plays.
_STATUS: dict[str, int] = {
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.TITLE_PREPARING: 503,
    ErrorCode.INTERNAL_ERROR: 503,
    PLAYBACK_UNAVAILABLE: 503,
}
_RETRY_STATUSES = frozenset({503})


@dataclass(frozen=True, slots=True)
class PlayRequest:
    user: User
    device: Device
    title: TitleRef
    extension: str  # as requested, lower case; apps request container_extension (mp4)
    client_ip: str | None
    user_agent: str = ""


@dataclass(frozen=True, slots=True)
class PlayStarted:
    url: str  # the absolute signed edge URL; never carries the Xtream credentials


@dataclass(frozen=True, slots=True)
class PlayRefused:
    code: str  # a stable code such as CONCURRENCY_LIMIT or SUBSCRIPTION_EXPIRED
    retry_after_s: int | None = None


type PlayOutcome = PlayStarted | PlayRefused


class PlaybackStarter(Protocol):
    def start(self, request: PlayRequest) -> PlayOutcome:
        """Check the entitlement (SPEC §7.4 order), take a slot, sign the edge URL."""
        ...

    def active_streams(self, user_id: str) -> int:
        """Streams the user is watching now (user_info.active_cons)."""
        ...


class UnavailablePlayback:
    """Playback before the playback service is wired in: refuses, retry later."""

    def start(self, request: PlayRequest) -> PlayOutcome:
        return PlayRefused(PLAYBACK_UNAVAILABLE, retry_after_s=DEFAULT_RETRY_AFTER_S)

    def active_streams(self, user_id: str) -> int:
        return 0


@dataclass(frozen=True, slots=True)
class Refusal:
    status: int
    reason: str
    retry_after_s: int | None


def refusal(outcome: PlayRefused) -> Refusal:
    status = _STATUS.get(outcome.code, 403)
    retry = outcome.retry_after_s
    if status in _RETRY_STATUSES and retry is None:
        retry = DEFAULT_RETRY_AFTER_S
    return Refusal(status=status, reason=outcome.code, retry_after_s=retry)


_starter: PlaybackStarter = UnavailablePlayback()


def playback_starter() -> PlaybackStarter:
    return _starter


def set_playback_starter(starter: PlaybackStarter) -> PlaybackStarter:
    """Install `starter` (at startup, or in tests); returns the previous one."""
    global _starter
    previous, _starter = _starter, starter
    return previous
