"""Live session records in redis-state: `sess:<session>` hashes and the `sess:index`
zset (SPEC §7.4). `start_playback` writes them, stream-auth refreshes them through
`concurrency.heartbeat`, the sweeper and stops remove them, and the admin feed
reads them. A record outlives its slot: it stays while a token for the session can
still reach the edge, so a seek late in a long progressive response still finds it.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import cast

import redis.asyncio

from apps.core.stores import state_redis
from apps.playback.concurrency import INDEX_KEY, sess_key

type RawRecord = Mapping[bytes, bytes]


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session: str
    row: str  # PlaybackSession id
    user: str
    device: str
    title: str  # title ref, "movie:<uuid>"
    rendition: str
    delivery: str  # progressive | segmented
    started: float
    seen: float
    exp: int  # expiry of the newest token
    idle: int  # seconds without activity after which the session ends
    bytes: int
    ip: str
    country: str
    edge: str
    max_streams: int
    policy: str
    user_name: str
    device_name: str
    title_name: str


def parse_record(session: str, raw: RawRecord) -> SessionRecord | None:
    """A record from HGETALL; None when it is missing or incomplete."""
    text = {key.decode(): value.decode() for key, value in raw.items()}
    try:
        record = SessionRecord(
            session=session,
            row=text["row"],
            user=text["user"],
            device=text["device"],
            title=text.get("title", ""),
            rendition=text.get("rendition", ""),
            delivery=text.get("delivery", ""),
            started=float(text["started"]),
            seen=float(text["seen"]),
            exp=int(text.get("exp") or 0),
            idle=int(text.get("idle") or 0),
            bytes=int(text.get("bytes") or 0),
            ip=text.get("ip", ""),
            country=text.get("country", ""),
            edge=text.get("edge", ""),
            max_streams=int(text.get("max_streams") or 1),
            policy=text.get("policy", ""),
            user_name=text.get("user_name", ""),
            device_name=text.get("device_name", ""),
            title_name=text.get("title_name", ""),
        )
    except (KeyError, ValueError):
        return None
    return record if record.row and record.user and record.device else None


def write_record(record: SessionRecord, *, ttl_s: int) -> None:
    """Store the whole record (replacing any older one) and index it."""
    fields = asdict(record)
    session = fields.pop("session")
    key = sess_key(session)
    pipe = state_redis().pipeline(transaction=True)
    pipe.delete(key)
    pipe.hset(key, mapping={name: str(value) for name, value in fields.items()})
    pipe.expire(key, max(1, ttl_s))
    pipe.zadd(INDEX_KEY, {session: record.seen})
    pipe.execute()


def read_record(session: str) -> SessionRecord | None:
    raw = cast("RawRecord", state_redis().hgetall(sess_key(session)))
    return parse_record(session, raw)


def index_before(cutoff: float) -> list[str]:
    """Sessions whose last activity is older than `cutoff` (sweeper candidates)."""
    members = cast("list[bytes]", state_redis().zrangebyscore(INDEX_KEY, "-inf", f"({cutoff!r}"))
    return [member.decode() for member in members]


async def live_records(client: redis.asyncio.Redis) -> list[SessionRecord]:
    """Every indexed session with a usable record, oldest activity first."""
    sessions = [member.decode() for member in await client.zrange(INDEX_KEY, 0, -1)]
    if not sessions:
        return []
    pipe = client.pipeline(transaction=False)
    for session in sessions:
        pipe.hgetall(sess_key(session))
    raws = await pipe.execute()
    records = (parse_record(session, raw) for session, raw in zip(sessions, raws, strict=True))
    return [record for record in records if record is not None]
