"""UUIDv7 identifiers (RFC 9562).

They're time-ordered, so primary-key B-tree indexes stay compact and inserts
stay append-mostly. Python 3.14 adds `uuid.uuid7()`; switch to it when we move.
"""

import os
import time
import uuid

_MASK_48 = (1 << 48) - 1
_MASK_62 = (1 << 62) - 1


def uuid7() -> uuid.UUID:
    """48-bit Unix ms timestamp | version 7 | 12 random bits | RFC variant | 62 random bits."""
    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    rand_a = (rand >> 62) & 0xFFF
    rand_b = rand & _MASK_62
    value = (unix_ms & _MASK_48) << 80 | 0x7 << 76 | rand_a << 64 | 0b10 << 62 | rand_b
    return uuid.UUID(int=value)
