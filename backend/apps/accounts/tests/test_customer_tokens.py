"""The bearer-token store for app clients (`customer_tokens`; ADR-0013)."""

from uuid import uuid4

import pytest

from apps.accounts import customer_tokens as tokens
from apps.accounts.customer_tokens import RefreshOutcome
from apps.core.stores import state_redis

pytestmark = pytest.mark.django_db  # settings overrides live in the database


def test_issue_then_authenticate() -> None:
    user, device = uuid4(), uuid4()
    pair = tokens.issue(user, device)
    grant = tokens.authenticate(pair.access_token)
    assert grant is not None
    assert (grant.user_id, grant.device_id, grant.family) == (user, device, pair.family)
    assert tokens.authenticate("siptv_at_" + "x" * 43) is None
    assert tokens.authenticate(pair.refresh_token) is None
    # Nothing in Redis holds a token in clear.
    for key in state_redis().scan_iter("ctok:*"):
        assert pair.access_token.encode() not in key
        assert pair.refresh_token.encode() not in key


def test_rotation_and_reuse_detection() -> None:
    user, device = uuid4(), uuid4()
    first = tokens.issue(user, device)
    rotation = tokens.rotate(first.refresh_token)
    assert rotation.outcome is RefreshOutcome.OK
    assert rotation.pair is not None
    assert rotation.user_id == user
    assert tokens.authenticate(rotation.pair.access_token) is not None
    # The old access token keeps working until its TTL: same family.
    assert tokens.authenticate(first.access_token) is not None

    reuse = tokens.rotate(first.refresh_token)
    assert reuse.outcome is RefreshOutcome.REUSE
    assert reuse.device_id == device
    assert tokens.authenticate(rotation.pair.access_token) is None
    assert tokens.rotate(rotation.pair.refresh_token).outcome is RefreshOutcome.INVALID


def test_invalid_refresh_tokens() -> None:
    assert tokens.rotate("nope").outcome is RefreshOutcome.INVALID
    assert tokens.rotate(tokens.REFRESH_PREFIX + "unknown").outcome is RefreshOutcome.INVALID


def test_revoking_a_user_ends_every_family() -> None:
    user = uuid4()
    devices = [uuid4(), uuid4()]
    pairs = [tokens.issue(user, device) for device in devices]
    assert sorted(tokens.revoke_user(user)) == sorted(devices)
    for pair in pairs:
        assert tokens.authenticate(pair.access_token) is None
        assert tokens.rotate(pair.refresh_token).outcome is RefreshOutcome.INVALID
    assert tokens.revoke_family("unknown") is None
