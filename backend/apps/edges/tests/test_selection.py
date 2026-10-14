"""Edge selection (ADR-0022): who may take a session, and the weighted, sticky choice."""

import secrets
from collections import Counter

from hypothesis import given
from hypothesis import strategies as st

from apps.edges.selection import Choice, EdgeView, Request, choose, eligible

LOCAL = EdgeView("local", "local", "disk", "", kids=frozenset())
REMOTE = EdgeView(
    "media2", "standalone", "origin", "https://media2.example.test", kids=frozenset({"k1"})
)
MIRROR = EdgeView(
    "mirror", "standalone", "disk", "https://mirror.example.test", kids=frozenset({"k1"})
)
CDN = EdgeView("bunny", "cdn", "disk", "https://cdn.example.test", origin="local")


def ask(**overrides: object) -> Request:
    fields: dict[str, object] = {
        "session": "a" * 32,
        "user_id": "u1",
        "live": False,
        "published_key": "asset-g1",
        "signing_kid": "k1",
    }
    fields.update(overrides)
    return Request(**fields)  # type: ignore[arg-type]


def ids(choice: Choice) -> str:
    return choice.edge.edge_id


def test_only_the_local_edge_is_todays_behaviour() -> None:
    choice = choose([LOCAL], ask())
    assert (ids(choice), choice.storage, choice.fallback) == ("local", "disk", "")


def test_live_tv_stays_on_the_control_host() -> None:
    assert ids(choose([LOCAL, MIRROR, REMOTE], ask(live=True))) == "local"
    alone = choose([REMOTE], ask(live=True))
    assert (ids(alone), alone.fallback) == ("local", "no_eligible_edge")


def test_an_origin_edge_serves_published_renditions_only() -> None:
    by_id = {edge.edge_id: edge for edge in (LOCAL, REMOTE)}
    assert eligible(REMOTE, ask(), by_id)
    assert not eligible(REMOTE, ask(published_key=""), by_id)
    choice = choose([REMOTE], ask())
    assert (ids(choice), choice.storage) == ("media2", "origin")


def test_unhealthy_disabled_draining_or_weightless_edges_are_skipped() -> None:
    by_id = {"local": LOCAL}
    for edge in (
        EdgeView("x", "standalone", "disk", "u", healthy=False, kids=frozenset({"k1"})),
        EdgeView("x", "standalone", "disk", "u", enabled=False, kids=frozenset({"k1"})),
        EdgeView("x", "standalone", "disk", "u", draining=True, kids=frozenset({"k1"})),
        EdgeView("x", "standalone", "disk", "u", weight=0, kids=frozenset({"k1"})),
    ):
        assert not eligible(edge, ask(), by_id)


def test_a_standalone_edge_must_hold_the_signing_key() -> None:
    by_id = {"local": LOCAL}
    assert not eligible(MIRROR, ask(signing_kid="k2"), by_id)
    assert eligible(MIRROR, ask(signing_kid="k1"), by_id)
    # The local edge reads the key file itself.
    assert eligible(LOCAL, ask(signing_kid="k9"), by_id)


def test_an_edge_over_its_capacity_takes_no_new_sessions() -> None:
    busy = EdgeView(
        "busy", "standalone", "disk", "u", kids=frozenset({"k1"}),
        capacity_bps=1e9, egress_bps=0.95e9,
    )  # fmt: skip
    calm = EdgeView(
        "calm", "standalone", "disk", "u", kids=frozenset({"k1"}),
        capacity_bps=1e9, egress_bps=0.5e9,
    )  # fmt: skip
    by_id: dict[str, EdgeView] = {}
    assert not eligible(busy, ask(), by_id)
    assert eligible(calm, ask(), by_id)
    assert eligible(busy, ask(max_utilisation=1.0), by_id)


def test_canary_edges_serve_their_customers_only() -> None:
    canary = EdgeView(
        "new", "standalone", "disk", "u", kids=frozenset({"k1"}),
        canary=True, canary_users=frozenset({"u7"}),
    )  # fmt: skip
    quiet_local = EdgeView("local", "local", "disk", "", weight=0)
    assert ids(choose([quiet_local, canary], ask(user_id="u7"))) == "new"
    assert ids(choose([canary], ask(user_id="u1"))) == "local"


def test_a_cdn_zone_follows_its_origin_and_needs_its_key() -> None:
    by_id = {"local": LOCAL, "bunny": CDN}
    assert not eligible(CDN, ask(cdn_ready=False), by_id)
    assert eligible(CDN, ask(cdn_ready=True), by_id)
    sick = EdgeView("local", "local", "disk", "", healthy=False)
    assert not eligible(CDN, ask(cdn_ready=True), {"local": sick, "bunny": CDN})
    assert choose([sick, CDN], ask(cdn_ready=True)).fallback == "no_eligible_edge"


def test_the_same_session_lands_on_the_same_edge() -> None:
    edges = [LOCAL, MIRROR, EdgeView("third", "standalone", "disk", "u", kids=frozenset({"k1"}))]
    session = secrets.token_hex(16)
    first = ids(choose(edges, ask(session=session)))
    assert all(ids(choose(edges, ask(session=session))) == first for _ in range(5))


def test_weights_split_new_sessions() -> None:
    heavy = EdgeView("heavy", "standalone", "disk", "u", weight=300, kids=frozenset({"k1"}))
    light = EdgeView("light", "standalone", "disk", "u", weight=100, kids=frozenset({"k1"}))
    counts = Counter(ids(choose([heavy, light], ask(session=f"{n:032x}"))) for n in range(4000))
    share = counts["heavy"] / 4000
    assert 0.70 < share < 0.80


def test_losing_an_edge_moves_only_its_sessions() -> None:
    a = EdgeView("a", "standalone", "disk", "u", kids=frozenset({"k1"}))
    b = EdgeView("b", "standalone", "disk", "u", kids=frozenset({"k1"}))
    c = EdgeView("c", "standalone", "disk", "u", kids=frozenset({"k1"}))
    sessions = [f"{n:032x}" for n in range(500)]
    before = {s: ids(choose([a, b, c], ask(session=s))) for s in sessions}
    after = {s: ids(choose([a, b], ask(session=s))) for s in sessions}
    moved = [s for s in sessions if before[s] != after[s]]
    assert all(before[s] == "c" for s in moved)


@given(st.lists(st.integers(min_value=0, max_value=1000), min_size=1, max_size=6), st.text())
def test_choose_always_answers_an_edge_it_was_given_or_the_local_one(
    weights: list[int], session: str
) -> None:
    edges = [
        EdgeView(f"e{i}", "standalone", "disk", "u", weight=w, kids=frozenset({"k1"}))
        for i, w in enumerate(weights)
    ]
    choice = choose(edges, ask(session=session))
    assert choice.edge.edge_id in {e.edge_id for e in edges} | {"local"}
    if any(weights):
        assert choice.edge.weight > 0
