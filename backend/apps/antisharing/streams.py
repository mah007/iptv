"""The networks a running stream is fetched from (G-18; ADR-0021).

stream-auth (async, Redis only) adds the client's /24 or /64 to `sessnets:<session>` in
redis-state, capped and expiring; `services.collect_stream_networks` (the 5-minute score
job) turns them into sightings. Nothing here may fail a heartbeat.
"""

from typing import cast

from redis.exceptions import RedisError

from apps.antisharing.services import (
    STREAM_NETS_MAX,
    STREAM_NETS_PREFIX,
    STREAM_NETS_TTL_S,
    network_of,
)
from apps.playback import concurrency


async def note_network(session: str, ip: str | None) -> None:
    network = network_of(ip)
    if network is None or network == "local":
        return
    key = f"{STREAM_NETS_PREFIX}{session}"
    try:
        client, _script = concurrency.async_state()
        if int(cast("int", await client.scard(key))) < STREAM_NETS_MAX:
            await client.sadd(key, network)
        await client.expire(key, STREAM_NETS_TTL_S)
    except RedisError:
        return
