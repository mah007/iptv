import uuid
from unittest import mock

from apps.core.ids import uuid7


def test_version_and_variant_follow_rfc_9562() -> None:
    value = uuid7()
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_embeds_the_unix_millisecond_timestamp() -> None:
    with mock.patch("apps.core.ids.time.time_ns", return_value=1_700_000_000_123_456_789):
        value = uuid7()
    assert value.int >> 80 == 1_700_000_000_123


def test_later_milliseconds_sort_later() -> None:
    with mock.patch("apps.core.ids.time.time_ns", return_value=1_000_000_000):
        earlier = uuid7()
    with mock.patch("apps.core.ids.time.time_ns", return_value=2_000_000_000):
        later = uuid7()
    assert earlier < later


def test_values_are_unique() -> None:
    assert len({uuid7() for _ in range(10_000)}) == 10_000
