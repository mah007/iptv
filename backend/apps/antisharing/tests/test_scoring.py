"""The anti-sharing score on fixed timelines (SPEC §11.1; ADR-0021)."""

from datetime import UTC, datetime, timedelta

from apps.antisharing import scoring
from apps.antisharing.scoring import Limits, Seen

NOW = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)
LIMITS = Limits()


def seen(
    network: str,
    asn: int | None,
    country: str | None,
    *,
    ago_min: int = 10,
    span_min: int = 5,
    device: str | None = "tv",
) -> Seen:
    last = NOW - timedelta(minutes=ago_min)
    return Seen(device, network, asn, country, last - timedelta(minutes=span_min), last)


def signal(assessment: scoring.Assessment, name: str) -> scoring.Signal:
    return next(item for item in assessment.signals if item.name == name)


def test_one_household_on_two_networks_is_fine() -> None:
    # The TV on home broadband and a phone on mobile data, same country.
    result = scoring.assess(
        [
            seen("77.31.4.0/24", 25019, "SA", device="tv"),
            seen("77.32.9.0/24", 39891, "SA", device="phone"),
        ],
        NOW,
        LIMITS,
    )
    assert result.score == 0
    assert not result.flagged


def test_churn_within_one_provider_is_ignored() -> None:
    # The ISP hands the TV a new address in another /24 every so often.
    timeline = [
        seen(f"77.31.{n}.0/24", 25019, "SA", ago_min=10 + 15 * n, span_min=10) for n in range(6)
    ]
    result = scoring.assess(timeline, NOW, LIMITS)
    assert signal(result, "networks_per_credential_1h").value == 1
    assert result.score == 0


def test_one_login_in_many_places_at_once_is_flagged() -> None:
    timeline = [
        seen("77.31.4.0/24", 25019, "SA", device="tv"),
        seen("41.32.1.0/24", 8452, "EG", device="tv"),
        seen("5.30.2.0/24", 5384, "AE", device="tv"),
        seen("81.2.69.0/24", 20712, "GB", device="tv"),
    ]
    result = scoring.assess(timeline, NOW, LIMITS)
    assert result.flagged
    assert signal(result, "asns_1h").value == 4
    assert signal(result, "countries_24h").value == 4
    assert signal(result, "networks_per_credential_1h").value == 4
    assert signal(result, "impossible_travel").value == scoring.MAX_TRAVEL_EVENTS


def test_two_networks_of_one_provider_used_together_count_twice() -> None:
    timeline = [
        seen("77.31.4.0/24", 25019, "SA", ago_min=10, span_min=30),
        seen("77.31.200.0/24", 25019, "SA", ago_min=5, span_min=30),
        seen("77.31.90.0/24", 25019, "SA", ago_min=8, span_min=30),
    ]
    assert scoring.networks_per_credential(timeline) == 3


def test_impossible_travel() -> None:
    riyadh_then_london = [
        seen("77.31.4.0/24", 25019, "SA", ago_min=120, span_min=5),
        seen("81.2.69.0/24", 20712, "GB", ago_min=60, span_min=5),  # ~4,900 km in an hour
    ]
    events = scoring.impossible_travel(riyadh_then_london, LIMITS)
    assert len(events) == 1
    assert {events[0].from_country, events[0].to_country} == {"SA", "GB"}
    # A real flight: ten hours apart is fine.
    flight = [
        seen("77.31.4.0/24", 25019, "SA", ago_min=700, span_min=5),
        seen("81.2.69.0/24", 20712, "GB", ago_min=60, span_min=5),
    ]
    assert scoring.impossible_travel(flight, LIMITS) == []
    # Neighbours closer than travel_min_km never count.
    neighbours = [
        seen("77.31.4.0/24", 25019, "QA", ago_min=10),
        seen("5.30.2.0/24", 5384, "BH", ago_min=10),
    ]
    assert scoring.impossible_travel(neighbours, LIMITS) == []


def test_local_and_old_sightings_are_left_out() -> None:
    timeline = [
        Seen("tv", "local", None, None, NOW - timedelta(minutes=5), NOW),
        seen("41.32.1.0/24", 8452, "EG", ago_min=60 * 30),  # 30 hours ago
        seen("77.31.4.0/24", 25019, "SA"),
    ]
    result = scoring.assess(timeline, NOW, LIMITS)
    assert signal(result, "asns_24h").value == 1
    assert result.score == 0


def test_limits_and_threshold_come_from_the_settings() -> None:
    timeline = [
        seen("77.31.4.0/24", 25019, "SA", device="tv"),
        seen("41.32.1.0/24", 8452, "SA", device="phone"),
        seen("5.30.2.0/24", 5384, "SA", device="tablet"),
    ]
    assert not scoring.assess(timeline, NOW, LIMITS).flagged
    strict = Limits(max_asns_1h=1, max_asns_24h=1, flag_score=4)
    assert scoring.assess(timeline, NOW, strict).flagged


def test_distances() -> None:
    km = scoring.distance_km("SA", "GB")
    assert km is not None
    assert 4_000 < km < 6_000
    assert scoring.distance_km("SA", "ZZ") is None
    assert scoring.Assessment(0, False, ()).as_dict() == {
        "score": 0,
        "flagged": False,
        "signals": [],
        "travel": [],
    }
