"""Bytes the edges served per playback session (G-17's second half; ADR-0024).

An edge can't know a response's size when it asks stream-auth (before the response),
so it counts afterwards: every media response adds its `$bytes_sent` to the session in
the edge's shared memory (njs, `bytes.js`), and every 15 s the edge reports what piled
up, `{"sessions": {"<session>": bytes}}`:

- the local edge to `POST /internal/stream-bytes` (`X-Edge-Auth`, like stream-auth);
- a standalone edge inside its control poll (`session_bytes`, apps.edges).

`add` puts them on the session's live record (`sess:<session>`, field `bytes`, which
stream-auth's `X-Bytes` from the live relay feeds too), Redis only. A session whose
record is gone (closed meanwhile: a whole file pulled in one request ends after the
last heartbeat) gets its bytes in `LATE_KEY` instead, and the sweeper adds those to the
closed row (`drain_late`). Live and catch-up streams aren't counted by the edge: the
live relay reports its own.
"""

import re
from typing import Final, cast

from apps.core.stores import state_redis
from apps.playback import concurrency

LATE_KEY: Final = "playback:late-bytes"
MAX_SESSIONS: Final = 5000
MAX_BYTES: Final = 1 << 53
_SESSION = re.compile(r"[0-9a-f]{32}")

# KEYS[1]: the late-bytes hash. ARGV[1]: the record prefix; then session, bytes pairs.
_ADD = """
local added, late = 0, 0
for i = 2, #ARGV, 2 do
  local record = ARGV[1] .. ARGV[i]
  if redis.call("EXISTS", record) == 1 then
    redis.call("HINCRBY", record, "bytes", ARGV[i + 1])
    added = added + 1
  else
    redis.call("HINCRBY", KEYS[1], ARGV[i], ARGV[i + 1])
    late = late + 1
  end
end
return {added, late}
"""

# Everything in the late-bytes hash, emptied in the same step.
_DRAIN = """
local items = redis.call("HGETALL", KEYS[1])
redis.call("DEL", KEYS[1])
return items
"""


def clean(raw: object) -> dict[str, int]:
    """The valid `{session: bytes}` pairs of a report (at most MAX_SESSIONS; others dropped)."""
    if not isinstance(raw, dict):
        return {}
    sessions: dict[str, int] = {}
    for session, sent in raw.items():
        if len(sessions) >= MAX_SESSIONS:
            break
        if (
            isinstance(session, str)
            and _SESSION.fullmatch(session)
            and isinstance(sent, int)
            and not isinstance(sent, bool)
            and 0 < sent <= MAX_BYTES
        ):
            sessions[session] = sent
    return sessions


def add(sessions: dict[str, int]) -> tuple[int, int]:
    """Add served bytes to the sessions' records. Returns (on records, kept as late)."""
    if not sessions:
        return 0, 0
    args: list[str | int] = [concurrency.SESS_PREFIX]
    for session, sent in sessions.items():
        args += [session, sent]
    reply = cast("list[int]", state_redis().register_script(_ADD)(keys=[LATE_KEY], args=args))
    return int(reply[0]), int(reply[1])


def drain_late() -> dict[str, int]:
    """The bytes of sessions that had closed when they were reported (the hash empties)."""
    flat = cast("list[bytes]", state_redis().register_script(_DRAIN)(keys=[LATE_KEY]))
    late: dict[str, int] = {}
    for index in range(0, len(flat) - 1, 2):
        session = flat[index].decode()
        try:
            late[session] = min(int(flat[index + 1]), MAX_BYTES)
        except ValueError:
            continue
    return late
